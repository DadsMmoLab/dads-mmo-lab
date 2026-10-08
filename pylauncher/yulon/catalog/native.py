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

import difflib
import functools
import io
import json
import math
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Collection, Generator, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from secrets import token_hex
from typing import Any, ClassVar, Literal, Protocol, cast

from yulon import (
    __version__,
    ansi,
    database_presence,
    dbsecret,
    docker,
    git,
    module_answers,
    networking,
    platform,
    realm_flag,
    resources,
    runner,
    server_build_presses,
    serverlock,
)
from yulon.after_stop import PutBackAfterStop, TrueAfterStop, withdraw_stop
from yulon.catalog import (
    bot_count,
    build_context,
    composegen,
    preflight,
    snapshot,
    time_zone,
    upstream,
    world_data,
)
from yulon.catalog.catalog import (
    CatalogEntry,
    EmulatorSource,
    NativeInstall,
    ReadyMarkers,
    SqlPhase,
    SqlPlan,
)
from yulon.catalog.git_head import read_head_file
from yulon.catalog.installer import (
    MEASURED_BUILD_TIMES,
    DockerUnavailableError,
    InstallerError,
    InstallOptions,
    InstallStopped,
    OneShotLeftRunning,
    ReadyWaitStopped,
    RollbackNotDone,
    SelfExplainedError,
    UnsupportedPlatformError,
    UpdateRefused,
    WorldStoppedAfterReadyError,
    default_server_dir,
    docker_unavailable,
    generated_compose_files,
    provision_lines,
    unsupported_platform_message,
)
from yulon.catalog.preflight import Spent
from yulon.log import get_logger
from yulon.manifest import Db
from yulon.ownership import Ownership as Ownership
from yulon.said import SaidByYulon, carry_detail
from yulon.support.redact import Redactor
from yulon.ui import lines

logger = get_logger(__name__)

STATE_FILE = ".yulon-install.json"

ERROR_RUN_INSTALL = "install"
"""`InstallState.error_run` for a failure of `run()`, the install (T207)."""

ERROR_RUN_REBUILD = "rebuild"
"""`InstallState.error_run` for a failure of any press on a remembered server (T207)."""
STATE_VERSION = 1

OUR_OWN_FILES = (STATE_FILE, networking.INTENT_FILE, module_answers.ANSWERS_FILE)
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
in one place rather than in the five call sites that ask the question. The third
is T104's record of the answers a player gave a module's questions.
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

PARKED_TAG_SUFFIX = "-parked"
"""What a finished build is KEPT as, when its rebuild replaced no container (T224).

Owner answer D1 (2026-10-04): a rebuild whose compile finished and which then
ended before any container was replaced -- Docker silent through the wait, a
Stop, a refused tag put right -- puts the live tags back on the build from
before and keeps the new one as `<ref>-parked`, one per image of
`composegen.built_image_refs()`, beside `PARKED_BUILD_FILE`, which records the
image ids and the fingerprint of the files it was made from. The next Rebuild,
Update or Return to the tested pin uses it instead of compiling when that
fingerprint is the same (D2). Start, Restart, Recreate and Install never use it:
they name only the live `<ref>` tags. A suffix on the TAG, for
`ROLLBACK_TAG_SUFFIX`'s reason. Not transient: it outlives the press on purpose,
and Uninstall removes it with the rest (`purge.Uninstaller`).
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


def no_rollback_confirmation(entry: CatalogEntry) -> str:
    """The confirmation's half of T170: what Rebuild does when the images' names are gone.

    Said in EVERY Rebuild confirmation (`installer.rebuild_confirmation()`), and
    conditionally, because the dialog is composed on the GUI thread and asks no
    daemon: the reading that would make it unconditional is a `docker image
    inspect` per image -- on a server inside a WSL distro, a boot of the distro --
    for a question the player may decline. The engine says which case it met,
    unconditionally, the moment it applies (`_keep_rollback()`, `NO_ROLLBACK_KEPT`).
    Until the owner's decision of 2026-09-28 this case was refused; the
    adversarial review of 2026-09-08 had found the press going ahead against a
    confirmation that promised a rollback, and this is what lets it go ahead
    without contradicting the dialog the player said yes to.

    Reset to default is named only where it reads the image (the CMaNGOS and
    TrinityCore families, `reset_defaults._from_image()`): WotLK's defaults are not in its
    image, so there it never says the image is gone. What each failure leaves
    is said separately, because a compile that fails, or a replace Docker
    refuses, leaves the server as it is, and only a new build that has replaced
    the containers leaves it down (cold review, T170 round 2).
    """
    block = entry.install.native
    says = (
        " (Reset to default says so when it finds the image gone)"
        if block is not None and block.family in ("cmangos", "trinitycore")
        else ""
    )
    return (
        f"If this server's images have lost their names on this machine{says}, the build its "
        "containers are running is kept as the rollback instead, found by its image. If there "
        "are no such containers either, there is no build to keep, and this compiles without a "
        "rollback: a compile that fails or is stopped, or containers Docker will not replace, "
        "leave the server as it is now; a new build that has replaced the containers and does "
        "not come up cannot be put back, and the server stays down until a rebuild comes up."
    )


NO_ROLLBACK_KEPT = (
    "This install's images are not all on the daemon under their tags, and no container of this "
    "install still holds a whole build, so there is no build to keep as a rollback: this rebuild "
    "compiles without one, as its confirmation said. If the compile fails or is stopped, or "
    "Docker will not replace the containers, they stay as they are now. If the new build "
    "replaces them and its server does not come up, there is no build from before to put back, "
    "and the server stays down until a rebuild comes up."
)
"""What a Rebuild says when it goes ahead with the images gone (T170), before the compile.

The owner's decision of 2026-09-28 ("Repair + Rebuild both"): after a confirm,
Rebuild compiles when the image is missing instead of refusing. The confirmation
said it first (`installer.rebuild_confirmation()`); this is the same fact at the
moment it applies, in the panel the rest of the press is read in, and it says
what each failure leaves -- the three sentences below are those failures'."""

NO_ROLLBACK_NOT_BUILT = (
    "No build was kept as a rollback, because this install's images were not all on the daemon. "
    "The containers were not replaced, so they are as they were before this rebuild, and its "
    "images are still not all there. Once the reason is fixed, press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)} again."
)
"""T170: a compile with no rollback that failed, or was stopped, before it finished."""

NO_ROLLBACK_UNTOUCHED = (
    "No build was kept as a rollback, because this install's images were not all on the daemon. "
    "The compile finished and its images are on the daemon under this install's tags, but the "
    "containers were not replaced, so they are as they were before this rebuild. Once the "
    "reason is fixed, press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)} again."
)
"""T170: the compile finished and the recreate refused before it touched a container."""

STOPPED_AS_THE_BUILD_FINISHED = (
    "The rebuild was stopped as its build finished, before any container was replaced."
)
"""T225: the failure a restore's sentence starts with when a Stop landed after compose tagged.

Not `_cancelled_message("the install")`, which `_check_cancel()` raises and which
names the wrong press; what follows it is the restore's account of the tags."""

NO_ROLLBACK_BUILT = (
    "No build was kept as a rollback, because this install's images were not all on the daemon, "
    "so there was none to put back: the containers now run the new build, and the server is "
    "down until it comes up. The log above says what stopped it."
)
"""T170: the containers were replaced with the new build and its server did not come up."""


_MUST_BE_FORCED = "must be forced"
"""The daemon's own words for "the only thing in the way is a stopped container".

Docker says `conflict: unable to delete <id> (must be forced) - container <id>
is using its referenced image` for a container that has EXITED, and `(cannot be
forced)` for one that is running -- measured live on the first restore, and
pinned in `FAILED_TAG_SUFFIX` above. `_let_go()` matches on the first because it
is the one that a second ask with `-f` answers; the second is a server somebody
is using, and no name is worth taking an image out from under it.
"""

COMPOSE_STAGE = "generate-compose"
"""The stage that writes this install's three compose files.

Named here because T64 selects on it: a family whose core checkout IS the
server directory has its `docker-compose.yml` overwritten by this stage, so
that path is one the app owns rather than the user, and a route that resets the
checkout has to write it again (`StagedInstaller.app_written_paths()`).
"""

DOCKERFILE_STAGE = "write-dockerfile"
"""The stage that renders this install's build recipe from the app's own templates.

TWO files, and the name says one: `_write_dockerfile()` renders `Dockerfile`
and `.dockerignore` through a single `dockerfile.write()` that rewrites whichever
differs and refuses either that carries no marker. Both user sentences say
"build recipe" and name the pair, because a `.dockerignore` behind its template
is rewritten by this press exactly as the Dockerfile is, and a dialog promising
"nothing else in the folder" was wrong about the second one (review, round 1).

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

DOCKER_PATIENCE_S = 180.0
"""How long a rebuild's recreate waits for a Docker that does not answer (T223, owner D2).

One `docker info` with a 10-second bound was the whole question until T223, and
on yulon-win11 (2026-10-04) it went unanswered right after an 85-minute compile
had exported its image: the restore that followed deleted the new build, while
the `docker tag` calls it made seconds later all worked. Docker was busy, not
gone. Three minutes is the time this app already gives Docker Desktop to start
(`platform._DOCKER_READY_TIMEOUT_SECONDS`).
"""

DOCKER_PATIENCE_POLL_S = 5.0
"""How often Docker is asked again during `DOCKER_PATIENCE_S`, and so how soon a Stop is read."""

HUB_RETRY_S = 30.0
"""The pause before the one retry of a build that could not reach Docker Hub (T223, owner D3).

The failure it answers costs seconds -- BuildKit looks the base image up before
anything is compiled -- and the one seen (a TLS handshake timeout to
auth.docker.io, yulon-win11, 2026-10-04) is the kind that has passed half a
minute later.
"""


def rebuild_opening_note(*, renders_dockerfile: bool, parked: bool = False) -> str:
    """What a rebuild costs and what it leaves alone, said before the first stage.

    `OPENING_NOTE`'s counterpart, and a separate sentence rather than a reuse
    because almost none of that one is true here: a rebuild clones nothing,
    generates no compose files, fetches no source and runs no import. Its own
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

    **"It does not fetch anything" was false, and said so until T223.** Every
    family builds `FROM` a registry image, and BuildKit asks Docker Hub about
    it at the start of a build -- on Docker Desktop's containerd image store
    even when the image is already here -- and a `RUN` layer whose cache is
    gone downloads its packages again. A Rebuild on yulon-win11 (2026-10-04)
    failed in seconds on a Docker Hub TLS timeout under a note promising it
    would fetch nothing. What stays true is narrower: no new source code.

    `parked` (T224): a kept build is recorded, and the compile clause says it may
    be used instead -- one clause still, so the count stays what it was.
    """
    compiles = (
        "compiles the server again from the source and modules in the folder below, or, if the "
        "folder has not changed since the kept build was made, uses that build instead"
        if parked
        else "compiles the server again from the source and modules in the folder below"
    )
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
        f"{compiles}, it replaces "
        "the running containers so the new build is what starts, and it waits for the server to "
        "come back up. It fetches no new source code, though Docker may go online while it "
        "builds: to ask Docker Hub about the base image the build starts from, and to download "
        "system packages its cache no longer holds. It does not rewrite your settings, and does "
        "not touch your database — your characters, accounts and the module SQL already applied "
        "are not read or written by this. The server is DOWN from the moment the containers are "
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

REPAIR_DATABASE_OPENING_NOTE = (
    "This makes the server's databases again from its own files, the way the install did, then "
    "starts the server. Accounts and characters are not in those files; a backup brings them back."
)
"""Said once at the top of a database Repair (T377)."""

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
whole feature turns on: three rounds of implementation tried to
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


@dataclass(frozen=True)
class LatestRoute:
    """The four parts of the "update to latest" control, wired so they cannot arrive apart.

    `UpdateRoute`'s argument, with two presses instead of one: a tab holding a
    `press` and no `confirmation` moves somebody's server source behind a dialog
    nobody wrote, and one holding `press` without `to_pin` offers a one-way door
    -- the way back off an untested commit is the same route aimed at
    `EmulatorSource.rev`, and a build that shipped one without the other would
    leave a user on code nobody tested with no control that returns them.
    `None` for the whole thing is the only other legal state and it hides the
    controls.
    """

    confirmation: Callable[[], str]
    """The update dialog's text for this install. Pure: nothing is fetched to compose it."""
    press: Callable[[threading.Event | None], Iterator[str]]
    """Cancel in, lines out: `RebuildSource`'s shape, for the same panel."""
    pin_confirmation: Callable[[], str]
    """The "Return to the tested pin" dialog's text."""
    to_pin: Callable[[threading.Event | None], Iterator[str]]
    """The same route aimed at each source's `rev` instead of at upstream's tip."""
    source_version: Callable[[], SourceVersion]
    """What this install was built from, and whether there is a pin to return to.

    A callable rather than a value, for `AdoptRoute.state`'s reason: it is a
    READING -- of the state file, on the tab's own schedule -- and a value
    computed when the tab was built would go stale the moment a press finished.
    It reads one small file and asks no daemon and no remote anything, so it is
    affordable on a reload; it never raises, for `git.head_version()`'s reason,
    because an exception here would take a tab down over a decoration.

    It answers BOTH questions because one reading has to settle both (T77). It
    returned the line alone until 2026-09-16, and the button's rule was
    "non-empty" -- which is true after a return as well as after an update, so
    the control stayed offered on a server that was already on the pin.
    """
    upstream_news: Callable[[], upstream.UpstreamNews] | None = None
    """How far upstream is past what this server was built from (T124), or None.

    NOT affordable on the GUI thread, unlike `source_version`: a reading that is
    not in the day's cache reads each source's HEAD through a container and asks
    GitHub over the network. The tab runs it through its job runner and draws
    `upstream.line()` of the answer. It never raises. `None` where the wiring
    offers no count -- a spy that predates it, or a route T125 has not given one.
    """


CORRECTIONS_BUTTON_LABEL = "Apply database corrections…"
SKIP_STUCK_LABEL = "Skip the missing file and continue"
"""The dialog's Yes when a stuck world update's file is gone from the checkout (T566)."""
"""T129's press, here for `UPDATES_BUTTON_LABEL`'s reason: the engine's refusals name it."""

CORRECTIONS_OPENING_NOTE = (
    "You can stop this at any time. This does three things and nothing else: it stops this "
    "install's world server if it is running or restarting, it starts the database on its own "
    "if it is down, and it applies the install-plan steps this version of Yu'lon has corrected "
    "since these databases were imported — only the ones marked safe to apply to a server that "
    "already has data -- recording each one that lands. It does not start the world server, "
    "compile, fetch, or re-run the rest of the install, and the completion marker is left as it "
    "is. If these databases do not carry that marker, this stops and says so rather than "
    "importing them."
)
"""What a corrections press costs, said before the first stage; `UPDATES_OPENING_NOTE`'s rule."""

CORRECTIONS_CANCEL_NOTE = (
    "A stop here leaves the statements that already ran in place and clears nothing; a step that "
    "did not finish is not recorded, so it is offered again."
)
"""`RERUN_CANCEL_NOTE`'s counterpart for T129's press: the gate already reads a finished import."""

CorrectionState = Literal["current", "stale", "held", "unknown", "unmarked", "unreadable"]


@dataclass(frozen=True)
class StuckWorldUpdate:
    """One world update a server's file ledger holds as `started` or `failed` (T545)."""

    phase: str
    file: str
    state: str
    """`started` (a press stopped while it ran) or `failed` (the database refused it)."""
    repeatable: bool
    """Every table it writes is dropped or emptied first (`sqlplan.whole_table_problem`), so
    running it again leaves what one run leaves. False is most content updates."""
    behind: int = 0
    """How many newer world updates of its phase wait behind it and run after it."""
    sha256: str = ""
    """The sha256 of the file's bytes when the dialog was built: the press runs those bytes only."""
    at_unix: int = 0
    """When its ledger row was written: the press takes the row only if it is still this one."""
    changed: bool = False
    """The file's bytes are not the ones the ledger row recorded when the update tried it."""
    heads: str = ""
    """Where the world-database checkouts stood when the dialog was built (T545): the press runs
    what is on disk then, so a checkout that moved since is a different press and is refused."""
    missing: bool = False
    """Its file is gone from the checkout and no other file holds its exact bytes (T566): it
    cannot be run again, and the dialog offers to skip it."""
    renamed_from: str = ""
    """Upstream renamed this file (T566): the ledger row is under this old name, and `file` is
    the one file now holding its exact bytes. The press moves the record to `file` and retries
    it there; nothing is asked."""


@dataclass(frozen=True)
class CorrectionCheck:
    """This install's SQL phases beside the ones this version of Yu'lon ships (T129).

    `stale` is the one state the Server tab offers "Apply database corrections…"
    on: at least one phase changed or added since the import, and declared
    `reapply_when_changed`. `held` is the same with nothing offerable -- every
    changed phase is withheld, which is logged and not bannered, because a
    banner with nothing to press would sit there for the life of the install.
    `unknown` is a marker from a plan no release shipped (so nothing is known
    about its phases), `unmarked` databases with no marker at all, and
    `unreadable` a question nobody answered. Each but `current` carries `why`.
    """

    state: CorrectionState
    offered: tuple[str, ...] = ()
    withheld: tuple[str, ...] = ()
    why: str = ""
    marker: str = ""
    """The marker's plan hash when this was read: the import the offer is a correction to."""
    baseline: tuple[tuple[str, str | None], ...] = ()
    """Each offered phase's recorded version when this was read (None: the install lacked it).

    `marker` and `baseline` are the provenance the person confirmed. The press
    reads the databases again and refuses whole unless both still say exactly
    this: an offer is agreement to change THIS record, not whatever is there by
    the time the press runs (Codex, T129 round 1)."""
    stuck: tuple[StuckWorldUpdate, ...] = ()
    """World updates an update to latest left `started` or `failed` (T545): the press runs
    each again, then the ones held back behind it. Part of the provenance: the press
    refuses whole unless the ledger still lists exactly these."""
    skip_missing: bool = field(default=False, compare=False)
    """The player chose "Skip" for the stuck updates whose file is gone (T566). The choice, not
    the reading: left out of `==`, so a press handed it still matches a fresh reading."""


@dataclass(frozen=True)
class CorrectionRoute:
    """The three parts of T129's control, wired for one install so they cannot arrive apart.

    `check` is a READING -- it asks the database, so the tab takes it once each
    time the database comes up, never on the poll -- and never raises.
    `confirmation` composes the dialog for the check the tab holds (it expands
    the folder, and can refuse with `InstallerError`). `press` is handed that
    same check -- what the person agreed to, provenance and all -- and refuses
    unless the databases still read the way it says.
    """

    check: Callable[[], CorrectionCheck]
    confirmation: Callable[[CorrectionCheck], str]
    press: Callable[[CorrectionCheck, threading.Event | None], Iterator[str]]


ComposeState = Literal[
    "current", "stale", "upstream", "follows", "foreign", "moved", "mixed", "missing", "error"
]


@dataclass(frozen=True)
class ComposeCheck:
    """This install's `docker-compose.yml` beside what this version of Yu'lon renders (T106).

    `stale` and `upstream` are the states the Server tab offers "Repair server
    files…" on. `upstream` (T170) is a WotLK checkout's own file, tracked and
    unmodified, in a folder Yu'lon's record says it built -- what a failed
    "Update the server to latest…" leaves when it cannot write Yu'lon's back.
    Every other state but `current` carries `why`, the sentence a refused
    repair says: Yu'lon's own file differing on a server whose Update rewrites
    it (`follows`, WotLK), a file that is not Yu'lon's (`foreign`), one that
    names another folder's project (`moved`), one whose host binds disagree
    about SELinux's `:z` (`mixed`), no file at all (`missing`), or a render or
    read that failed (`error`).

    `added`/`removed` count the lines a repair would add and take away, for the
    confirmation; both are 0 on anything but `stale` and `upstream`.

    `settings` and `kept` are T169's, and only on `stale`: each conf line the same
    repair sets so the server writes into a folder it binds (`LogsDir =
    "../logs" in etc/mangosd.conf`), and why a folder setting is left as the
    player has it. The confirmation names both.

    `world_data_gb` is T219's, and only on `stale`: non-zero when the repair adds
    the `world-data` volume a Windows Centurion world reads its map data from, the
    room it takes on Docker's disk; the confirmation then says the first start
    after the recreate copies the map data into it.
    """

    state: ComposeState
    why: str = ""
    added: int = 0
    removed: int = 0
    settings: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    world_data_gb: float = 0.0


@dataclass(frozen=True)
class ComposeRepaired:
    """What a repair did: the file, and its backup -- `None` when nothing needed writing."""

    path: Path
    backup: Path | None
    confs: tuple[Path, ...] = ()
    """The backup of each conf the repair set a folder setting in (T169), in table order."""


@dataclass(frozen=True)
class _ConfEdit:
    """One conf a repair sets folder settings in (T169): its exact text as read, and as written."""

    path: Path
    before: str
    after: str
    settings: tuple[str, ...]


@dataclass(frozen=True)
class ComposeRepairRoute:
    """The two halves of T106's control, wired for one install so they cannot arrive apart.

    `check` is a READING, asked off the GUI thread when the tab opens and on
    Refresh; it never raises. `repair` asks again at press time and writes only
    on `stale`, refusing everything else with the check's own sentence.
    """

    check: Callable[[], ComposeCheck]
    repair: Callable[[], ComposeRepaired]


@dataclass(frozen=True)
class ConfCheck:
    """The module confs this install lacks and could have written from their `.dist` (T137).

    `missing` is every conf the catalog's `confs_from_dist` names that is not on
    disk while its `.dist` is: the one state the Server tab offers "Repair server
    files…" on for it. Empty is "nothing to offer" -- the confs are there, or there
    is no `.dist` to make one from.
    """

    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfRepaired:
    """The confs a press wrote, relative to the server dir; empty when none needed writing."""

    written: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfRepairRoute:
    """T137's half of "Repair server files…", wired for one install: a reading and a press.

    `check` reads the disk and never raises. `repair` asks again at press time and
    writes only a conf that is still absent, never over one that is there.
    """

    check: Callable[[], ConfCheck]
    repair: Callable[[], ConfRepaired]


REPAIR_BACKUP_SUFFIX = ".repair.bak"
"""`docker-compose.yml.<stamp>.repair.bak`: the file as it was before a repair replaced it."""

_O_BINARY = getattr(os, "O_BINARY", 0)
"""Windows' untranslated-bytes flag for `os.open`; 0 (no such flag) everywhere else."""


def _backup_beside(path: Path, when: datetime) -> Path:
    """Copy `path` to a stamped `.repair.bak` beside it that did not exist, and return it.

    Microseconds in a fixed-width stamp so two presses are two files, and the next
    free microsecond on a clash rather than a counter suffix -- `tuning.backup()`'s
    rule, for its reason: a later copy must never land on an earlier one.

    The name is claimed with `O_CREAT | O_EXCL`, owner-only, so the copy can only
    ever land in a file THIS call made -- a conf's backup holds the database
    password, and is never readable by others while its bytes arrive -- and
    then takes `path`'s mode and times (`copystat`, as `copy2` gave them). A copy
    that fails part-way removes that file (Codex, T169 round 3): a truncated
    `.repair.bak` would claim to be the file as it was. An existing file at the
    name is never touched; the next microsecond is tried instead.
    """
    for _ in range(1000):
        target = path.with_name(f"{path.name}.{when:%Y%m%d-%H%M%S-%f}{REPAIR_BACKUP_SUFFIX}")
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
        except FileExistsError:
            when += timedelta(microseconds=1)
            continue
        try:
            with os.fdopen(fd, "wb") as out, path.open("rb") as source:
                shutil.copyfileobj(source, out)
            shutil.copystat(path, target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target
    raise OSError(f"no free name for a backup of {path.name}")


class ComposeChangedError(OSError):
    """The file changed between the press's check and its write; it was left as it is."""


def _replace_if_unchanged(path: Path, text: str, expected: str, *, exact: bool = False) -> None:
    """Put `text` at `path` in one step, with `path`'s mode -- only if it still says `expected`.

    `expected` is the text the press checked and backed up. The target is read
    again as the last thing before the rename, and anything else there (an editor
    saving, a second press) aborts the write with the file left as it is: what
    the press validated is not what it would be replacing. A small window stays
    between that read and the rename; two Yu'lon processes on one install are out
    of scope (no interprocess lock), and this closes the one a person can hit.

    The temp file is `tempfile.mkstemp`'s own, in the same folder, so a leftover
    of another attempt is never clobbered; it is fsynced, chmodded to the old mode
    BEFORE the rename (no moment at the umask default), and removed on every
    failure. `newline="\\n"`, as `composegen.write_plan()` writes every compose file.

    `exact` (T169, a conf): written and compared without newline translation,
    as the conf patcher reads and writes, so a CRLF conf stays CRLF and a check
    that read it exactly is not read back translated and called changed.

    Raises:
        ComposeChangedError: the file no longer says `expected`.
        OSError: the temp file could not be written or renamed.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".yulon-new", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="" if exact else "\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        now = _read_exact(path) if exact else path.read_text(encoding="utf-8")
        if now != expected:
            raise ComposeChangedError(f"{path.name} changed after it was checked")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _confs_already(done: Sequence[tuple[Path, Path]]) -> str:
    """The sentence a repair stopped after its conf edits ends with (T169, Codex round 2).

    Those confs stay set -- each with its old text in the named backup -- and
    the compose file is still `stale`, so the next press finishes the job; a
    restart before it would run the server with the setting and no bind.
    """
    if not done:
        return ""
    names = ", ".join(f"{conf.name} (its old text is {backup.name})" for conf, backup in done)
    return (
        f" Already changed: {names}. Press Repair server files… again before restarting "
        "the server, and it finishes the rest."
    )


def _read_exact(path: Path) -> str:
    """The file's text with its line endings as they are: the conf patcher's read (T169)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _lines_changed(old: str, new: str) -> tuple[int, int]:
    """How many lines going from `old` to `new` adds and removes, for the confirmation."""
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


WSL_DISTRO_STOPPED_NOTE = (
    "The server's WSL distro is stopped; Yu'lon will check for rewritten history once it starts."
)
"""Said under the update question when the distro is down (T125, wsl-resident-servers §2).

The question normally names any source whose upstream rewrote its history,
read from the upstream cache in the server folder -- and reading a
`\\\\wsl.localhost\\...` folder starts a stopped distro, which a question the
player may cancel must not do. The press itself still refuses an unnamed
divergence before it moves anything (T126), and names it the next time.
"""


def update_to_latest_confirmation(
    entry: CatalogEntry,
    server_dir: Path,
    repo: str,
    rewritten: Sequence[str] = (),
    *,
    copied: Sequence[str] = (),
    not_copied: Sequence[str] = (),
) -> str:
    """The one question asked before an update to latest. The approved design's own words.

    Three facts and no fourth, and every one of them is a fact rather than a
    reassurance -- `installer.rebuild_confirmation()`'s rule, applied to a
    press that is strictly more dangerous than a rebuild:

    * *what it is* -- code nobody has tested with this app. Said first and said
      plainly, because it is the whole difference from the Rebuild button beside
      it, which recompiles the commit the gates ran on.
    * *what it costs* -- `MEASURED_BUILD_TIMES`, the same citation the rebuild
      quotes, because it is the same compile.
    * *how it can fail, and what survives* -- the build is put back; what the new
      server wrote into the database on its first start is NOT. That second half
      is the recommendation the owner asked for, stated as the reason the offer
      of a backup exists rather than as advice with nothing behind it. It is
      true because `rebuild()`'s rollback covers images and `_put_sources_back()`
      covers the checkout, and neither of them covers a schema migration a
      worldserver ran at boot.

    `rewritten` are `rewritten_line()`s (T126): one per source whose newest
    release sits on history upstream rewrote. They are the only part of this
    question that is not always the same, and the press moves such a source
    only if its line was here.

    It names the repository being fetched from, because "the newest code" is not
    a thing a user can look at and the repository is: the sha in the log
    afterwards belongs to that repo, and so does the issue they will file.

    The sentence lives here rather than in the view for the reason every user
    sentence in this module does -- `ui/` may not author copy that has to be
    tested, and this has assertions on it that run without Qt.
    """
    said = "".join(f"\n\n{line}" for line in rewritten)
    # T217: `copied` is the family's copy of the databases its new build can change
    # (`StagedInstaller.snapshot_databases()`), put back if that build does not come
    # up; with none, the database half is the sentence it always was.
    if copied:
        names = _listed(copied)
        database = (
            f" If the new server starts and does not come up, Yu'lon also puts {names} back "
            f"{_as_it_was(copied)} just before the new server started: it copies "
            f"{'it' if len(copied) == 1 else 'them'} then, with your server stopped. The copy "
            "adds a few minutes to the time your server is down and takes some hundreds of MB "
            "in the server's backups folder; Yu'lon keeps only the newest."
        )
        if not_copied:
            database += (
                f" {_listed(not_copied)} {'is' if len(not_copied) == 1 else 'are'} not copied: "
                "what the new server writes into it on first start is not put back."
            )
        back = server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)
        database += (
            " The backup offered here is for later: if the new server does come up and you "
            f"go back with {back}, nothing undoes what it wrote."
        )
    else:
        database = (
            " Anything the new server writes into your database on first start is not put "
            "back — that is what the backup is for."
        )
    return (
        f"Update {entry.name} in {server_dir} to the newest {repo} code?\n\n"
        f"This builds code nobody has tested with this app. It takes as long as your first "
        f"build ({MEASURED_BUILD_TIMES}) and it can fail — a module may no longer compile, or "
        f"the new server may refuse your database. If the build fails, the build you have now "
        f"is put back.{database}{world_updates_note(entry)}{said}"
    )


def world_updates_note(entry: CatalogEntry) -> str:
    """The update's world-content clause, for an entry whose plan brings new world updates (T531).

    Read off the catalog (an `on_update: apply_new` phase), never off an id, and
    empty for every other entry. Said in both confirmations, because both presses
    move the world-database repository to the commit this app was tested with.
    """
    block = entry.install.native
    data = block.cmangos if block is not None else None
    modes = {phase.on_update for phase in data.sql.phases} if data is not None else set()
    said = ""
    if "apply_new" in modes:
        said += (
            f" It also applies the world content fixes this Yu'lon was tested with that your "
            f"{entry.databases.world} database does not have yet, each once, with the servers "
            "stopped; they stay applied even if the new build is put back."
        )
    if "replace_changed" in modes:
        # T534, on the lead's word of 2026-10-07: say what the reload discards.
        said += (
            " The first update loads every bot table fresh from its file, and later ones only "
            "those whose files changed, which replaces what the bots generated in them (travel "
            "routes, zone levels, named places)."
        )
    return said


def return_to_pin_confirmation(entry: CatalogEntry, server_dir: Path, repo: str) -> str:
    """The question asked before going back to the commit this app was tested against.

    The way out of the button above, and it is the SAME press with the target
    changed -- a full compile, the server down while the containers are
    replaced, and no undo for what the newer server already wrote into the
    database. So the copy says all three, in the same order and mostly in the
    same words, because a user who has read one has read the shape of the other
    and the differences are what should stand out.

    What it deliberately does not say is that this fixes anything. Going back to
    the tested commit restores the SERVER; a migration the newer build applied
    to a character database stays applied, and an older worldserver meeting a
    newer schema is its own evening. The backup taken before the update is
    what covers that, and this names it rather than implying the return does.
    """
    return (
        f"Put {entry.name} in {server_dir} back on the {repo} commit this app was tested "
        f"against?\n\n"
        f"This compiles the server again from that commit — the same wait as the update "
        f"({MEASURED_BUILD_TIMES}) — and your server is down while its containers are "
        f"replaced. It does NOT undo anything the newer server already wrote into your "
        f"databases; only the backup you took covers that.{world_updates_note(entry)} Say no "
        "and nothing happens at all."
    )


def commits_past_pin(rev: SourceRev) -> str:
    """One source's version line: what it was built from, and how far that is past the pin.

    The sentence the approved design specifies, and its three other shapes,
    each of which exists because the fact behind it is genuinely different:

    * `ahead` a positive number -- *Built from a1b2c3d (2026-09-16), 12 commits
      past the tested pin f82e7d6*. The pin is named because the number means
      nothing without the thing it counts from, and because that sha is what
      somebody pastes into an issue.
    * `ahead` zero -- *..., the tested pin*. Saying "0 commits past" of a
      checkout that is ON the pin is a figure where a plain fact belongs, and
      this is the line a press of "Return to the tested pin…" produces.
    * `ahead` `None` -- *...; the tested pin is f82e7d6*. Could not count, which
      a truncated history can genuinely produce, so the line states what it
      knows and claims no distance. It must not read as zero; see `SourceRev`.
    * no pin at all -- the distance clause is dropped entirely rather than
      written against an empty string.

    `built` is split on `git.VERSION_SEPARATOR` because that is what wrote it
    (`git.head_version()`), and a value that does not split that way is printed
    whole: it came off disk, it is somebody's install, and mangling it into
    "Built from  ()" would be worse than passing it through.
    """
    said = rev.built.split(git.VERSION_SEPARATOR)
    head = f"built from {said[0]} ({said[1]})" if len(said) == 2 else f"built from {rev.built}"
    if rev.release:
        # T126: a source that follows releases is named by the release it is
        # on, which is the name a player reads on the project's page.
        head = f"{rev.repo.rsplit('/', 1)[-1]} {rev.release}, {head}"
    if not rev.pin:
        return head
    short = rev.pin[:_SHORT_SHA]
    if on_its_pin(rev):
        # The whole sentence, not a clause bolted onto "built from …". After a
        # return the old line read *"built from 993f180 (2026-09-02); the
        # tested pin is 993f180"* -- the same two shas, and a reader had to
        # compare them character by character to learn the one thing the line
        # existed to tell them (T77, and the live gate's PARTIAL of
        # 2026-09-16). This says it instead.
        when = f" ({said[1]})" if len(said) == 2 else ""
        return f"on the tested pin {short}{when}"
    if rev.ahead is None:
        return f"{head}; the tested pin is {short}"
    if rev.ahead == 0:
        # Reachable only for a record whose `built` is not a sha this can read
        # against the pin -- `on_its_pin()` answers the sha question first.
        # Kept rather than folded in, because a zero that came off disk with an
        # unreadable `built` is still "no distance" and still must not print as
        # `None`'s "could not say".
        return f"{head}, the tested pin"
    plural = "commit" if rev.ahead == 1 else "commits"
    return f"{head}, {rev.ahead} {plural} past the tested pin {short}"


_SHORT_SHA = 7
"""How many characters of a sha this app shows, and the fewest it will compare.

`git.head_version()` abbreviates to seven and `commits_past_pin()` has printed
`pin[:7]` since T64, so seven is what a user sees on both sides of the line.
It is also the floor for `on_its_pin()`: a prefix shorter than this could match
a pin it is not, and the answer decides whether a button offering a multi-hour
compile is on screen.
"""


def on_its_pin(rev: SourceRev) -> bool:
    """Is this source standing on the commit the app was tested against? (T77)

    The one predicate, asked by the version line and by the "Return to the
    tested pin…" button, because the two must never disagree: a live Return
    over a line that says the server IS on the pin is the state the design
    forbids, and it is what shipped -- the button's rule read whether
    `source_revs` EXISTED, and `_record_source_revs()` writes it after a return
    just as it does after an update (live gate, 2026-09-16, press 6).

    **The sha is the authority and `ahead` can only refute it.** Seven of the
    nine shipped sources are shallow clones, where `commits_since()` cannot
    count at all and records `None` (T64, cold review round 1) -- so a rule
    written on `ahead == 0` would answer "not on the pin" for the very installs
    this is for. A counted, non-zero `ahead` still wins, because it is a
    measurement of the same two commits and a disagreement means the record is
    not to be trusted in the direction that offers the compile.

    `built` is `git.head_version()`'s string (`a1b2c3d · 2026-09-16`) and `pin`
    is the catalog's full 40 characters, so the comparison is a prefix one. A
    `built` that does not begin with at least `_SHORT_SHA` sha-ish characters --
    somebody's hand-edited file, a future format -- answers False, which keeps
    the button offered: the failure that costs an hour of compiling is better
    than the one that strands a user on untested code with no way back.
    """
    if not rev.pin:
        return False
    if rev.ahead:
        return False
    head = rev.built.split(git.VERSION_SEPARATOR)[0].strip()
    return len(head) >= _SHORT_SHA and rev.pin.startswith(head)


def past_the_tested_pin(state: InstallState | None) -> bool:
    """Is there anything to return FROM? The button's rule, read off the CONTENT.

    True while ANY row is off its pin, which is what makes the mixed case right:
    a WotLK install whose core went back and whose second source did not is
    still past the pin, and the press that finishes the job must stay offered.

    False for an install with no `source_revs` at all -- every install that has
    never pressed "Update the server to latest…" -- and false again the moment
    a return has rewritten every row. Pressing it there would fetch, move
    nothing, and recompile for the better part of an hour to arrive exactly
    where it already was.
    """
    if state is None or not state.source_revs:
        return False
    return any(not on_its_pin(rev) for rev in state.source_revs)


def source_revs_line(state: InstallState | None) -> str:
    """The Server tab's version line for this install, or `""` when it has none.

    Empty for every install still on its catalog pins, which is every install
    that has never pressed "Update the server to latest…" -- an install whose
    sources are where the gates put them has nothing to report that the
    catalog does not already say, and a line repeating it would be a reading
    of `catalog.json` dressed up as a reading of the folder.

    The repo is named ONLY when more than one source was moved, and that is not
    a cosmetic rule: one line beginning "Built from" is a statement about this
    server, and three of them with nothing to tell them apart would be three
    statements about an unknown subject. WotLK moves two sources, so its line
    is two rows; a single-source family reads as the design's sentence exactly.
    """
    return _revs_line(state.source_revs if state is not None else ())


def _revs_line(revs: Sequence[SourceRev]) -> str:
    """`source_revs_line()` over rows rather than a state: T588 reads them against the catalog."""
    if not revs:
        return ""
    if len(revs) == 1:
        # Not `.capitalize()`, which lower-cases everything after the first
        # character: today every character after it happens to be lower case
        # already, so the two agree, and the day a branch name or a repo slug
        # with a capital in it reaches this line they would not.
        said = commits_past_pin(revs[0])
        return said[0].upper() + said[1:]
    # A row that names its release names its source as well ("TortoiseBots
    # v2026-09-25, built from ..."), so the repo prefix would say it twice.
    return "\n".join(
        commits_past_pin(rev) if rev.release else f"{rev.repo}: {commits_past_pin(rev)}"
        for rev in revs
    )


@dataclass(frozen=True)
class SourceVersion:
    """What the tab draws about this install's sources, from ONE reading (T77).

    Two answers in one object because they come from one file and must not
    disagree: the line the user reads and whether there is anything to return
    from. Asked separately they would be two reads of a file a press can
    rewrite between them, and the disagreement's shape is exactly the defect
    this type was added for -- a live "Return to the tested pin…" over a line
    saying the server is on it.
    """

    line: str
    """The version line, or `""` for an install still on its catalog pins."""
    past_the_pin: bool
    """Whether "Return to the tested pin…" has anything to do."""
    pin_moved: bool = False
    """Whether a source is off a tested pin the catalog MOVED since this server was built (T588).

    Only then is the return a way onto the commits this version of Yu'lon was
    tested with rather than a way back off an update, which is what lets the
    Modules tab's report name it as a remedy for a module that needs other
    server code (T586).
    """


def source_version(
    state: InstallState | None,
    off: Sequence[SourceOff] = (),
    pins: Sequence[CatalogPin] = (),
) -> SourceVersion:
    """Both halves of the version line, from one `InstallState`.

    Deliberately takes the state rather than reading it: the read is the
    caller's (`install_wiring`), and a function that read the file itself could
    not be handed the three shapes a test needs.

    `off` (T217, the live check of 65315f9c) are the folders a failed update left
    off their build (`sources_still_off()`). While there are any, every start is
    refused with a sentence naming "Return to the tested pin…", so the press is
    offered whatever the record says, and each such folder's row says the commit
    it is really on: after the put-back the record reads "on the tested pin" for
    a folder that is not.

    `pins` (T588) are the catalog's pins now and each checkout's HEAD
    (`tested_pins()`): the record is read against THEM, so an install whose pin
    the app moved is offered the press, and one an update left exactly on the
    new pin is not (`_against_the_catalog()`). Empty, the record alone decides,
    as before T588.
    """
    if not off:
        read, moved = _against_the_catalog(state.source_revs if state is not None else (), pins)
        line = _revs_line(read)
        if moved:
            line = f"{line}\n{PIN_MOVED_NOTE}"
        # A row for a repo the press no longer moves (the catalog renamed or dropped
        # it) is history on the line, not a reason to offer a compile that cannot
        # move it. Without a catalog reading every row counts, as before T588.
        moves = {pin.repo for pin in pins}
        counted = [row for row in read if not moves or row.repo in moves]
        return SourceVersion(
            line=line,
            past_the_pin=any(not on_its_pin(row) for row in counted),
            pin_moved=bool(moved),
        )
    by_repo = {row.repo: row for row in off}
    rows = source_revs_line(state).splitlines() if state is not None else []
    revs = state.source_revs if state is not None else ()
    if len(revs) == 1 and rows:
        rows = [f"{revs[0].repo}: {rows[0][0].lower()}{rows[0][1:]}"]
    lines: list[str] = []
    for rev, row in zip(revs, rows, strict=False):
        lines.append(_off_row(by_repo.pop(rev.repo)) if rev.repo in by_repo else row)
    lines.extend(_off_row(row) for row in by_repo.values())
    return SourceVersion(line="\n".join(lines), past_the_pin=True)


def _off_row(row: SourceOff) -> str:
    """One version-line row for a folder off its build: what it is on, and that it is off."""
    now = f"the folder is on {row.head[:7]}, " if row.head else "the folder is "
    return (
        f"{row.repo}: {now}not on {row.built[:7]}, the commit this server was built from, so "
        "it is off its build"
    )


@dataclass(frozen=True)
class CatalogPin:
    """One moving source's tested commit as THIS build of Yu'lon pins it, and its checkout (T588).

    `rev` is `EmulatorSource.rev` read off the catalog now, which is not the
    `SourceRev.pin` an earlier update press recorded: an app update can move
    the catalog's pin (T389 moved WotLK's core from 7f12e89e to f19a1879) and
    the record cannot know. `head` is what `.git/HEAD` says, read the way
    `sources_still_off()` reads it (`read_head_file()`: no git run, no
    network), so the Server tab can ask it on every reload.
    """

    repo: str
    rev: str
    head: str | None
    """The checkout's commit, or None when `.git/HEAD` cannot be read as one."""


def tested_pins(server_dir: Path, sources: Sequence[EmulatorSource]) -> tuple[CatalogPin, ...]:
    """Each moving source's catalog pin and checkout; `()` when one of them pins nothing.

    `()` then because "Return to the tested pin…" refuses an entry that does not
    pin every source it moves (`update_to_latest(to_pin=True)`), so there is no
    move to offer.
    """
    if any(not source.rev for source in sources):
        return ()
    return tuple(
        CatalogPin(source.repo, source.rev or "", read_head_file(server_dir / source.dest))
        for source in sources
    )


def _against_the_catalog(
    revs: Sequence[SourceRev], pins: Sequence[CatalogPin]
) -> tuple[tuple[SourceRev, ...], tuple[CatalogPin, ...]]:
    """The record's rows read against the catalog's pins, and the pins this server can catch up to.

    Three cases per source, and only the last two are T588's:

    * a row whose recorded pin IS the catalog's: unchanged, today's reading;
    * a row whose recorded pin is not (the pin moved since that press): read
      against the catalog's pin, its count dropped -- it was counted against
      the old pin, and a count against the wrong commit is worse than none;
    * no row at all (an install that never pressed an update): the checkout
      sits on the pin it was installed at, so a HEAD that is not the catalog's
      pin is a pin that moved. A HEAD nobody could read says nothing: offering
      a compile on "cannot say" is the failure that costs an hour.

    A moved pin is returned only while its source is OFF it: an update that
    landed exactly on the new pin is on it, and offered nothing.

    **And only while the press is a catch-up for every source it moves** (cold
    review). A source still on the pin it was installed or returned at (no row,
    or a row built on its old recorded pin) moves onto the commits this app is
    tested with. A source an update took past its pin -- one past the old pin,
    or past an unchanged one -- moves BACK, a downgrade, and then nothing is
    returned: the press keeps the way-back wording, which warns that nothing
    undoes what the newer server wrote.
    """
    by_repo = {pin.repo: pin for pin in pins}
    rows: list[SourceRev] = []
    moved: list[CatalogPin] = []
    back = False
    for row in revs:
        pin = by_repo.pop(row.repo, None)
        if pin is None or row.pin == pin.rev:
            rows.append(row)
            # Off an unchanged pin is past it: only an update puts it there.
            back = back or (pin is not None and not on_its_pin(row))
            continue
        now = replace(row, pin=pin.rev, ahead=None)
        built = _built_sha(row)
        if pin.head is not None and not (built and pin.head.startswith(built)):
            # The record is from before the pin moved and the folder is not where it
            # says: the folder is the fact. A record left stale (a hand checkout, a
            # state write that failed) must not offer a compile onto the commit the
            # folder is already on (Codex adversarial). Unread, the record decides.
            now = replace(now, built=pin.head[:_SHORT_SHA])
        rows.append(now)
        if on_its_pin(now):
            continue
        on_the_old_pin = bool(_built_sha(now)) and row.pin.startswith(_built_sha(now))
        if on_the_old_pin:
            moved.append(pin)
        else:
            back = True
    for pin in by_repo.values():
        if pin.head is None or pin.head == pin.rev:
            continue
        now = SourceRev(pin.repo, pin.head[:_SHORT_SHA], pin=pin.rev)
        rows.append(now)
        if not on_its_pin(now):
            moved.append(pin)
    return tuple(rows), () if back else tuple(moved)


def _built_sha(row: SourceRev) -> str:
    """The sha half of a record's `built` (`a1b2c3d · 2026-09-16`), or `""` when it has none."""
    head = row.built.split(git.VERSION_SEPARATOR)[0].strip()
    return head if len(head) >= _SHORT_SHA else ""


def moved_pins(state: InstallState | None, pins: Sequence[CatalogPin]) -> tuple[CatalogPin, ...]:
    """The sources whose catalog pin moved since this server was built, and that are off it."""
    return _against_the_catalog(state.source_revs if state is not None else (), pins)[1]


PIN_MOVED_NOTE = (
    "The commit this version of Yu'lon was tested with has changed since this server was built: "
    f"{server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)} moves the "
    "server onto it and builds it."
)
"""Under the version line when a catalog pin moved past (or away from) this server (T588)."""


def _on_and_tested(moved: Sequence[CatalogPin]) -> str:
    """`repo is on abc1234, tested with def5678`, per moved source, as one clause."""
    return "; ".join(
        (
            f"{pin.repo} is on {pin.head[:_SHORT_SHA]}, tested with {pin.rev[:_SHORT_SHA]}"
            if pin.head
            else f"{pin.repo} is tested with {pin.rev[:_SHORT_SHA]}"
        )
        for pin in moved
    )


def moved_pin_confirmation(
    entry: CatalogEntry, server_dir: Path, moved: Sequence[CatalogPin]
) -> str:
    """The "Return to the tested pin…" question when the pin moved under this server (T588).

    `return_to_pin_confirmation()` is the way BACK off an update, and says so:
    "back", and "anything the newer server already wrote". Neither is true of a
    move onto a tested commit the app moved to, so this one says what the move
    is without a direction (the reading has none: it is local, see
    `CatalogPin`), and the module order T586 is about: the compile takes every
    module as it is on disk.
    """
    return (
        f"Move {entry.name} in {server_dir} onto the commits this version of Yu'lon was tested "
        f"with?\n\nThey have changed since this server was built ({_on_and_tested(moved)}). This "
        f"compiles the server from them ({MEASURED_BUILD_TIMES}), and your server is down while "
        "its containers are replaced. If the build fails, the build you have now is put back. "
        "Once the new server has come up, nothing undoes what it writes into your databases; a "
        f"backup you take first covers that.{world_updates_note(entry)}\n\nThe build compiles your "
        "modules as they are: if a module has an update written for these commits, update it on "
        f"the Modules tab first, without pressing “{server_build_presses.REBUILD}” in between. Say "
        "no and nothing happens at all."
    )


def core_off_its_moved_pin_note(moved: Sequence[CatalogPin], named: Sequence[str]) -> str:
    """Said after a Rebuild that failed in a module while the server is off a moved pin (T586).

    The Discord player's evening: mod-ale updated to a commit that calls the
    newer core's `IsHeadless()`, Rebuild pressed on the old core, the compile
    fails in mod-ale and mod-ale is put back. The press that builds the newer
    tested core WITH the module is "Return to the tested pin…", and the module
    has to be on its update when it runs -- so the order is said.
    """
    mods = _listed(named)
    one = len(named) == 1
    return (
        f"This server is not on the commits this version of Yu'lon was tested with "
        f"({_on_and_tested(moved)}), and {mods} may have been written for those commits. Press "
        f"{server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)}: it builds "
        f"the tested commits with your modules. If Yu'lon put {mods} back above, update "
        f"{'it' if one else 'them'} on the Modules tab again first, without pressing "
        f"“{server_build_presses.REBUILD}” in between."
    )


def module_order_note(named: Sequence[str], press: str) -> str:
    """Said after "Update the server to latest…" or "Return to the tested pin…" failed in a module.

    The other half of T586: an old mod-ale on the new core fails the same way
    the new mod-ale fails on the old one. The press put the server's sources
    back; what can change the outcome is the module's own update, made first,
    with no Rebuild between (a Rebuild on the old core would fail on it and put
    it back again).
    """
    mods = _listed(named)
    one = len(named) == 1
    return (
        f"The build stopped on an error in {mods}, which may not build on the new server code "
        f"yet. If {mods} {'has an update' if one else 'have updates'}, update "
        f"{'it' if one else 'them'} on the Modules tab first, without pressing "
        f"“{server_build_presses.REBUILD}” in between, then press "
        f"{server_build_presses.under_server_build(press)} again."
    )


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


def correctable_phases(plan: SqlPlan) -> tuple[SqlPhase, ...]:
    """The phases of one plan a corrections press may apply: `reapply_when_changed` (T129).

    The ONE filter, `rerunnable_phases()`'s rule: the wiring that offers the
    control and `sqlplan.phase_drift()` that decides what is offered read the
    same flag, and this is where the spine reads it.
    """
    return tuple(phase for phase in plan.phases if phase.reapply_when_changed)


def correction_phases(entry: CatalogEntry) -> tuple[SqlPhase, ...]:
    """`correctable_phases()` for an entry; empty means the control is not offered at all.

    Read off the catalog, `update_phases()`'s way: `wow-tbc` and `wow-vanilla`
    answer `spell_template hotfix`, `wow-tortoise` its `character_inventory_copy
    table` (T159) and WotLK nothing, and a test enumerates the catalog so that
    stays a fact about data.
    """
    native_block = entry.install.native
    block = native_block.cmangos if native_block is not None else None
    if block is None:
        return ()
    return correctable_phases(block.sql)


_STOPS_THE_WORLD = (
    "If the world server is running -- or restarting over and over after a crash -- this "
    "press STOPS it first, before anything is written: a running world server holds these "
    "tables in memory and writes back over whatever it finds in them. Anyone playing is "
    "disconnected, and it saves as it does on Stop. It is left stopped: press Start on the "
    "Server tab when this has finished. The database alone is started if it is down; the "
    "world server is never started by this."
)


NEWER_WORLD_CONTENT_WAITS = (
    "Yu'lon's newer world content waits for "
    f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)}."
)
"""Said after a retry press left a world-database checkout short of this Yu'lon's pin (T545)."""


def stuck_world_updates_text(stuck: Sequence[StuckWorldUpdate]) -> str:
    """What the corrections dialog says about world updates an update left unfinished (T545).

    Each file by name, what happened to it, and whether running it again is shown to be
    safe; a file that is not is the person's to check first -- the dialog's Yes is the
    consent T531 said no press would assume.
    """
    lines = []
    for one in stuck:
        what = (
            "the database refused it" if one.state == "failed" else "an update stopped while it ran"
        )
        after = (
            f" {one.behind} newer world update(s) waiting behind it run after it, in order."
            if one.behind
            else ""
        )
        if one.missing:
            lines.append(
                f"    {one.file} -- {what}. Its file is no longer in the sources, so it cannot be "
                f"run again.{after}"
            )
            continue
        safe = (
            "It empties every table it writes first, so running it again is safe."
            if one.repeatable
            else "Yu'lon cannot show that running it again is safe: it may have run part way, "
            "and running it again could put rows in twice. Check what it does before you say yes."
        )
        if one.changed and not one.repeatable:
            safe += (
                " The file has also changed since the update tried it, so the database may hold "
                "what the older version did."
            )
        if one.renamed_from:
            safe = (
                f"It is the same file as {one.renamed_from}, under a new name: Yu'lon moves its "
                f"record to the new name and runs it there. {safe}"
            )
        lines.append(f"    {one.file} -- {what}. {safe}{after}")
    text = (
        "World updates an update to latest did not finish, which this runs again (each one, "
        "then the ones held back behind it):\n\n" + "\n".join(lines)
    )
    if any(one.missing for one in stuck):
        text += (
            f'\n\n"{SKIP_STUCK_LABEL}" records each file that is no longer in the sources as '
            "skipped, so it is never run, and then runs the world updates behind it. Whatever "
            "such a file already changed in your world database stays as it is: Yu'lon does "
            "not undo it. Cancel changes nothing."
        )
    return text


def corrections_banner_text(check: CorrectionCheck) -> str:
    """The Server tab banner for a `stale` reading: corrected steps, unfinished updates, or both."""
    said = []
    if check.withheld and not check.offered:
        said.append(
            f"{', '.join(check.withheld)} also changed in this version, and only a new install "
            "gets it."
        )
    if check.offered:
        held = (
            f" ({', '.join(check.withheld)} also changed, and only a new install gets "
            f"{'it' if len(check.withheld) == 1 else 'them'}.)"
            if check.withheld
            else ""
        )
        said.append(
            f"This version of Yu'lon corrects {', '.join(check.offered)} in this server's install "
            f"plan, and these databases were imported before that.{held}"
        )
    if check.stuck:
        names = ", ".join(one.file.rsplit("/", 1)[-1] for one in check.stuck)
        said.append(f"A world update did not finish on this server: {names}.")
    what = "applies it" if not check.stuck else "runs it again, with the ones after it"
    return (
        " ".join(said) + f" {CORRECTIONS_BUTTON_LABEL} {what} -- it asks first, names every step, "
        "and stops the world server first if it is up. Nothing changes until you press it."
    )


def corrections_confirmation(
    entry: CatalogEntry,
    server_dir: Path,
    offered: Sequence[str],
    files: Sequence[str],
    withheld: Sequence[str],
    stuck: Sequence[StuckWorldUpdate] = (),
) -> str:
    """What the user agrees to before a corrections press (T129). Pure, for Qt-free assertions.

    The step list is the `expand()` output the press streams: a file by its path
    as the run's log names it, a literal statement by its phase's name. The
    withheld phases are named too: the person is told what this version changed
    that a press will NOT put on their server, and why, rather than finding out
    from a log.
    """
    listing = "\n".join(f"    {name}" for name in files)
    held = (
        f"Also changed since then, and NOT applied by this: {', '.join(withheld)}. Applying those "
        f"again over a server that already has data is not known to be safe — they drop, "
        f"re-create or overwrite -- so only a new install gets them.\n\n"
        if withheld
        else ""
    )
    if not offered:
        return f"Run the unfinished world updates of {entry.name} again?\n\n" + (
            f"Folder: {server_dir}\n\n{stuck_world_updates_text(stuck)}\n\n{held}"
            f"{_STOPS_THE_WORLD}"
        )
    extra = f"{stuck_world_updates_text(stuck)}\n\n" if stuck else ""
    return (
        f"Apply the corrected install-plan steps to {entry.name}?\n\n"
        f"Folder: {server_dir}\n\n"
        f"This version of Yu'lon has corrected {', '.join(offered)} since these databases were "
        f"imported. This applies {len(files)} SQL step(s), the whole of "
        f"{'that step' if len(offered) == 1 else 'those steps'}, into this install's "
        f"databases:\n\n"
        f"{listing}\n\n"
        f"Each is marked in the install plan as safe to apply again to a server that already has "
        f"data, and nothing else in the plan is re-run. Your databases keep their completion "
        f"marker; what is written besides the steps is the record of which version of each step "
        f"they now have, so a step is not offered again once it has landed. Your characters, "
        f"accounts and world are not otherwise written.\n\n"
        f"{held}"
        f"{extra}"
        f"{_STOPS_THE_WORLD}\n\n"
        f"If a step is refused, a step whose plan says to stop on errors stops the press there; "
        f"one whose plan says to warn and carry on does so. Either way a step that did not land "
        f"whole is not recorded, and is offered again."
    )


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
        f"only to learn which state they are in. Nothing records which version of each step of "
        f"the install plan these databases have, because nothing here can know it, so an adopted "
        f"server is never offered a corrected install-plan step later (T129).\n\n"
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

CORRECTIONS_WAIT_HINT = (
    'Stop ends this here and applies nothing: the world keeps running. "Stop now anyway" '
    "stops the world regardless and goes on with the corrections; it may then be force-stopped "
    "and lose what happened since its last save."
)
"""Said once under a load wait in the corrections press (T159): `REBUILD_WAIT_HINT`'s shape,
for the press whose stop comes before anything is touched."""

REBUILD_WAIT_HINT = (
    "Stop ends the rebuild here and replaces nothing: the server keeps running the build it "
    'had. "Stop now anyway" stops the world regardless and goes on with the replace; the '
    "world may then be force-stopped and lose what happened since its last save."
)
"""Said once under a load wait before the replace (T158): what each of the panel's two
controls does at this point. Stop is the panel's Cancel."""

ROLLBACK_STOPPING = (
    "Stopping the new build (it may be force-stopped) and putting the previous build back\u2026"
)
"""The rollback's first line once the containers were replaced (T158, round 3, the lead's words).

The failed build is stopped before any tag moves back, and it may be force-stopped: if its world
is still loading, the rollback waits and "Stop now anyway" stops it regardless. It began "The
new build did not come up;" until T247's review: a build stopped by the player after its world
DID come up is put back too (`READY_STOPPED_AFTER_LOADING`), and the line was false there.
"""

OLD_BUILD_STOPPING = (
    "The build from before this update did not come up either; stopping its servers so they "
    "do not restart over and over."
)
"""Said before the old build that failed after a rollback is stopped (T217 live proof, item 5)."""

OLD_BUILD_WAIT_HINT = (
    "The build from before this update is still loading and cannot be stopped cleanly yet. "
    'Yu\'lon waits for it, or for it to restart; "Stop now anyway" or Stop stops it regardless '
    "-- it may then be force-stopped."
)
"""`ROLLBACK_WAIT_HINT` for the stop of the old build that did not come up (T217)."""

ROLLBACK_WAIT_HINT = (
    "The new build's world is still loading and cannot be stopped cleanly yet. Yu'lon waits "
    'for it; "Stop now anyway" stops it regardless -- it may then be force-stopped -- and the '
    "previous build is put back once it is down."
)
"""Said once under a load wait in the rollback (T158): there, Stop neither gives up the stop --
the rollback's job is to replace a build that already failed -- nor forces it (T247 review:
never kill a loading world on a plain Stop); "Stop now anyway" does."""


def _stop_control(ctx: StageContext, *, rollback: bool) -> docker.StopControl:
    """The load wait's controls for a stop the engine makes, from the job's Cancel (T158).

    The panel's "Stop now anyway" rides on the Cancel (`docker.CancelWithForce`).
    What the Cancel itself means depends on where the stop is:

    * before the replace, it GIVES UP the stop (`abandon`): nothing has been
      touched yet, and the server keeps the build it had;
    * in a rollback, it does nothing to the stop: the stop is not given up --
      the build being stopped has already failed, and giving up would leave
      it running with the tags half-way -- and it is not forced either. It
      FORCED it until the lead's ruling of 2026-10-05 (T247 review): a Stop
      that lands in a rebuild's ready wait is already set when the rollback
      stops the new world, and forcing on it killed that world in the middle
      of its load, which is when it updates its database (T158). Only "Stop
      now anyway" forces a rollback's stop now.
    """
    cancel = ctx.cancel
    force = cancel.anyway if isinstance(cancel, docker.CancelWithForce) else threading.Event()
    if rollback:
        return docker.StopControl(anyway=force)
    return docker.StopControl(anyway=force, abandon=cancel or threading.Event())


def _speaking(
    work: Callable[[docker.OutputSink], object], abandon: threading.Event
) -> Iterator[str]:
    """Run a blocking stop on a thread, yielding what it says as it says it (T158).

    A stage is a generator and its lines are the panel's output, while the
    load wait lives inside `docker`'s blocking stop functions. This joins the
    two: the stop's `say` is a queue this reads, so the wait is heard while it
    waits. A reader that goes away mid-wait (the generator closed) sets
    `abandon`, so the wait ends with nothing sent, and is waited for.
    Whatever the work raised is raised here.
    """
    lines: queue.Queue[str] = queue.Queue()
    failed: list[BaseException] = []

    def run() -> None:
        try:
            work(lines.put)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the reading side
            failed.append(exc)

    thread = threading.Thread(target=run, name="yulon-stop-wait", daemon=True)
    thread.start()
    try:
        while thread.is_alive() or not lines.empty():
            try:
                yield lines.get(timeout=0.25)
            except queue.Empty:
                continue
    finally:
        if thread.is_alive():
            abandon.set()
            thread.join()
    if failed:
        raise failed[0]


def _with_hint(lines: Iterator[str], hint: str) -> Iterator[str]:
    """Pass `lines` through, and say `hint` once, under the first sentence of a load wait."""
    hinted = False
    for line in lines:
        yield line
        if line in docker.LOAD_WAIT_LINES and not hinted:
            hinted = True
            yield hint


BUILD_CANCEL_NOTE = (
    "Stopping now leaves Docker finishing the build step it is already on, in the background. "
    "That is deliberate: the work it has done is kept, and starting this install again picks up "
    "from there instead of compiling it all a second time."
)
"""What a Stop does DURING THE BUILD on macOS, said just before the build starts.

Abandoning the compose client does not abandon the daemon's work, and the
layer cache is precisely what makes a resume cheap — a user told "cancelled"
without this sentence reaches for `docker builder prune` and throws away the
thing that would have saved them three hours.

Said on macOS only since T298, which measured Linux (`BUILD_CANCEL_NOTE_ENDS`)
and could not reach a Mac: whether Docker Desktop there ends the build with the
CLI was not probed, so the older, cautious sentence stays. Ask
`build_cancel_note()`.
"""

BUILD_CANCEL_NOTE_ENDS = (
    "Stopping now ends the build at once. The steps it has already finished are kept in Docker's "
    "build cache, so starting this install again picks up from there instead of compiling it all "
    "a second time."
)
"""`BUILD_CANCEL_NOTE` on Windows and Linux, where a Stop was measured to end the build.

Windows (T246), yulon-win11 2026-10-05: with docker.exe's tree ended, BuildKit's
build went to `Error` within seconds and no image tag moved; before T246 the
orphaned compose and buildx processes finished it and moved the live tag
10 min 38 s after Stop.

Linux (T298), 2026-10-06, on yulon-ubuntu (Ubuntu's docker.io 29.1.3, compose
2.40.3) and m910q (Docker CE 29.7.2, compose v5.5.0): a compose build of a
60-second RUN step, stopped as `runner._end_child()` stops it -- SIGTERM to the
docker CLI alone, and separately SIGKILL -- left no compose or `buildx bake`
process 2 s later and never tagged its image, where the same build left alone
tagged it. The CLI closes the plugin's socket on either signal (a SIGKILL
closes it with the process), and compose ends its bake on that. The Steam Deck
runs the same Linux CLI and was not probed separately.

The cache half is said everywhere: the finished steps are what makes the next
press cheap.
"""


def build_cancel_note() -> str:
    """The build stage's cancel note for THIS platform, asked when the stages are built."""
    return BUILD_CANCEL_NOTE if sys.platform == "darwin" else BUILD_CANCEL_NOTE_ENDS


BUILDER_LOST = (
    "because it lost its connection to Docker's builder part-way through: Docker closed it. "
    "That happens when Docker's engine is restarted or killed under the build, and the commonest "
    "reason is Docker running out of memory while it compiles. To see how much memory Docker "
    "has (Total Memory), run this in a terminal:\n"
    "docker info\n"
    "On Windows, Docker Desktop's WSL 2 engine has no memory slider: Windows sizes it, through "
    "the memory= line in %UserProfile%\\.wslconfig. Change that line, run this, then start "
    "Docker Desktop again:\n"
    "wsl --shutdown\n"
    "On a Mac, and with Docker Desktop's "
    "Hyper-V engine, it is Settings → Resources → Memory; Docker Engine on Linux uses the "
    "machine's own memory. Then run it again: the steps the build had finished are kept in "
    "Docker's build cache, so it picks up from the last of them instead of starting over. If it "
    "happens again at the same place, restart Docker first."
)
"""T202: what a build that ended in `rpc error: code = Unavailable` is told.

The T179 live check met it twice on one Windows VM (2026-10-03): once after a
finished compile and 53 silent minutes, once mid-compile at 99%, and the build
passed after Docker was given more memory. Before this the player read "the
build failed (exit 1). Its last words were:" and a raw gRPC line. "Run it
again" rather than "press Install": the same stage serves Rebuild. Windows
first and in full because that is where it was seen, and because the Resources
pane there has no memory control on the WSL 2 engine (review, 2026-10-04).
"""

BUILD_QUIET_NOTICE_SECONDS = 10 * 60
"""How long a build may print nothing before the player is told so (T202).

Measured, not guessed: the longest quiet stretch inside four real build logs
(a CMaNGOS TBC install, three CMaNGOS Vanilla rebuilds, an AzerothCore
rebuild) was about a minute, a CMake configure under WSL; a step that ends is
followed by the next step's header within a second. The T179 stall that this
exists for printed nothing for 53 minutes.

**Silence is judged on output alone, on purpose.** The ticket proposed "no
output AND no Docker CPU/disk progress". The T179 stall had both at once --
Docker's VM at 0 % CPU and about 50 KB/s of disk writes for those 53 minutes
(live log, phase A7) -- but output alone already separates 60 seconds from
3,180 by a factor of fifty, and reading a VM's CPU differs on Docker Desktop
for Windows, for Mac, under WSL and on a bare Linux engine. And because the
watch only ever SAYS something, a false reading costs a sentence, never a build.
"""

BUILD_STALLED_SECONDS = 30 * 60
"""How long a build may print nothing before it is called stalled (T202).

Thirty times the longest quiet stretch measured, and still well short of the
53 minutes the T179 stall sat silent before Docker ended it with an EOF.

**Nothing is ended here, by the lead's ruling on review (2026-10-04).** A slow
image export can be quiet for long on a slow disk, so silence alone is not
proof of a hang. The ruling also cited that on Windows `proc.terminate()`
reached docker.exe only, leaving docker-compose.exe and docker-buildx.exe
running; T246 (2026-10-05) made a Stop end that whole tree, but the first reason
stands. So the player is told how to check and what to do, and restarting
Docker -- which the sentence asks for -- is what ends a hung build: the client
then fails with the EOF that `BUILDER_LOST` explains.
"""


def _minutes(seconds: float) -> str:
    whole = max(1, round(seconds / 60))
    return f"{whole} minute{'s' if whole != 1 else ''}"


def build_quiet_notice() -> str:
    """Said once a build has printed nothing for `BUILD_QUIET_NOTICE_SECONDS` (T202)."""
    return (
        f"The build has printed nothing for {_minutes(BUILD_QUIET_NOTICE_SECONDS)}. A working "
        "build is rarely quiet for more than a minute or two, but it is left running; if it is "
        f"still silent at {_minutes(BUILD_STALLED_SECONDS)} you are told how to check whether it "
        "has stalled."
    )


def build_stalled_notice() -> str:
    """Said once a build has printed nothing for `BUILD_STALLED_SECONDS` (T202). Ends nothing."""
    return (
        f"The build has printed nothing for {_minutes(BUILD_STALLED_SECONDS)} and looks stalled: "
        "a working build is never that quiet. It is still running, and Yu'lon will not stop it. "
        "To check, Docker's CPU use (Task Manager on Windows, top on Linux or a Mac) should not "
        "sit near zero, and this should answer at once:\n"
        "docker info\n"
        "If it has stalled, restart "
        "Docker Desktop (on Linux, the docker service): the build then ends, and pressing Install "
        "again -- or, for a rebuild, "
        f"{server_build_presses.under_server_build(server_build_presses.REBUILD)} -- resumes it, "
        "because the steps it finished are kept in Docker's build cache."
    )


@dataclass(frozen=True)
class QuietWatch:
    """`_pump()`'s silence watch: one notice after `notice_after`, one after `stalled_after`.

    Silence is "no line from the subprocess" (see `BUILD_QUIET_NOTICE_SECONDS`
    for why output alone). It SAYS things and ends nothing (`BUILD_STALLED_SECONDS`).
    """

    notice_after: float
    stalled_after: float
    notice: str
    stalled: str


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
`Added realm "Yulon ubuntu2" at 100.64.0.13:8085.` — because
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
    (`.notes/gates/bug41-loopback-2026-09-05/closing_step_driver_b41.py:121`)
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


UPDATE_SOURCES_STAGE = "update-sources"
"""The stage name T64's fetches report their progress under.

Never recorded in the state file, and it is not in any family's stage tuple: it
is a label on git's own percentages so the log panel's header strip attributes
them to something the user can see, exactly as `stage_clone_sources()` passes
`recorded_as`. A name that WAS in a stage tuple would be a name a resume could
decide to skip.
"""

UPDATE_TO_LATEST_OPENING_NOTE = (
    "This is code nobody has tested with this app. Every source is checked first -- it must be "
    "the repository this app cloned, with no changes and no commits of your own in it -- and if "
    "anything after that fails, every source is put back on the commit it is on now and the "
    "build you have keeps running."
)
"""What the route says before it fetches, under `rebuild_opening_note()`'s rule.

Every clause names something this method really does, in the order it does it,
and nothing here is a reassurance: the checks are `_refuse_unless_updatable()`,
the restore is `_put_sources_back()`, and "the build you have keeps running" is
`rebuild()`'s own rollback rather than a promise made on its behalf.
"""

RETURN_TO_PIN_OPENING_NOTE = (
    "This is the commit this app was tested against, and it is still a full build -- the same "
    "wait as the update, and your server is down while its containers are replaced. Every "
    "source is checked first -- it must be the repository this app cloned, with no changes and "
    "no commits of your own in it -- and if anything after that fails, every source is put back "
    "on the commit it is on now and the build you have keeps running."
)
"""What the route says before it fetches on the way BACK. The opposite fact, first.

The same three clauses as `UPDATE_TO_LATEST_OPENING_NOTE` in the same order,
because a user who has read one has read the shape of the other and the
difference is what should stand out -- and the difference is the first clause,
which is the only one that is not the same in both directions. Sharing the
update's note here told somebody returning to the gated commit that *"this is
code nobody has tested with this app"*, which is the opposite of true (live
gate, 2026-09-16, press 6).

What it adds rather than borrows is the cost, said here and not only in the
dialog: this is where a user finds out, having pressed, that they have an hour
to wait and a server that will go down inside it. What it does NOT say is that
this fixes anything -- the return restores the server and not the database, and
`return_to_pin_confirmation()` is where that is spelled out.
"""

SOURCES_KEPT_NOTE = (
    "The source folders were left on the new commits, since the build that was kept was made "
    "from them."
)
"""Appended when the rebuild KEPT the new build (`WorldStoppedAfterReadyError`, T71; T179).

Then the sources stay with it: putting the old commits back would leave the folder and
the running binary disagreeing, under a sentence saying they agree.
"""

SOURCES_LEFT_NOTE = (
    "The source folders were left on the new commits, because the build from before this "
    "update was not put back: what is on disk is what the new build was made from."
)
"""Appended when the rebuild's rollback stopped early (`RollbackNotDone`, T197).

The tags still name the new build, so a start runs it: the old commits put back under
it would disagree with every start, under `SOURCES_PUT_BACK_NOTE`'s "agree again".
"""

SOURCES_LEFT_UNTOUCHED = (
    "The source folders were left on the new commits, because the image tags name the new "
    "build made from them. None of your server's containers was replaced, so it is still "
    "running the build from before this update if it is up"
)
"""The first half of the note when no container was replaced (`RollbackNotDone.touched`).

The tags and the sources name the new build, the containers still hold the old one: both
said, because "what is on disk is what the new build was made from" alone reads as if the
new build were what runs now. `untouched_note()` says what the next Start does."""

SOURCES_LEFT_UNTOUCHED_NOTE = f"{SOURCES_LEFT_UNTOUCHED}, and its next Start runs the new build."
"""`SOURCES_LEFT_NOTE` when no container was replaced and nothing refuses a start (fix round 1).

Since T223 (the lead's ruling under owner answer D1) every untouched exit writes the
`untested` start refusal, so this is said only when that record could not be written --
`owe_start()`'s warning is then in the same message, and the sentence is true."""


def untouched_note(refused: str | None) -> str:
    """The untouched note, with what the next Start does: runs the new build, or is refused.

    Fix round 2: on Centurion the kept build's world tables are left waiting
    (`ServersDownWork.keep`), so the next Start is refused until they are in, and the
    note says so with the refusal's own sentence, which names the press that finishes it.
    """
    if refused is None:
        return SOURCES_LEFT_UNTOUCHED_NOTE
    return f"{SOURCES_LEFT_UNTOUCHED}, and Start is refused until this is done: {refused}"


SOURCES_MIXED_BACK = (
    "The source folders were put back on the commits they were on before this update."
)
"""The first sentence of `SOURCES_MIXED_NOTE`; `mixed_note()` may add one after it."""

SOURCES_MIXED_REBUILD = (
    "Its image tags are mixed, so it must be rebuilt before it can start, and Start is refused "
    "until it is: press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}, which compiles "
    "every image from those commits."
)
"""The last sentence of `SOURCES_MIXED_NOTE`: what the player does next."""

SOURCES_MIXED_NOTE = f"{SOURCES_MIXED_BACK} {SOURCES_MIXED_REBUILD}"
"""Appended when the rollback left the tags MIXED (`RollbackNotDone.mixed`, fix rounds 1-2).

No single build is on the tags, so there is no new build for the sources to stay with and
nothing new to record: the record is left as it was and the sources go back to the commits
they were on. `rebuild()` writes `START_REFUSED_FILE`, so every start refuses until a
Rebuild succeeds."""

MIXED_UNTOUCHED = (
    "None of your server's containers was replaced, so it is still running the build from "
    "before this update if it is up."
)
"""What a mixed-tags note adds when no container was replaced (fix round 3)."""


def mixed_note(touched: bool) -> str:
    """`SOURCES_MIXED_NOTE`, saying the old build still runs when no container was replaced."""
    if touched:
        return SOURCES_MIXED_NOTE
    return f"{SOURCES_MIXED_BACK} {MIXED_UNTOUCHED} {SOURCES_MIXED_REBUILD}"


START_REFUSED_FILE = ".yulon-start-refused.json"
"""A rollback left this server's image tags mixed: no start may run until a Rebuild (T197).

Read by every start: `Controller.refuse_start()` (Start, Start and play, the launcher's
PLAY, Restart, Recreate, the bot reload) and the engine's `start_refusal()`. Written in the
server folder beside the install record, so it outlives the app; `{"version":1,
"why":"rebuild"}`, and a record nobody can read refuses the same. Only a successful
Rebuild clears it (`rebuild()`), and only the Rebuild press is not refused by it; a
Rebuild that fails leaves the servers its rollback put back stopped (fix round 8), and
TrinityCore's "Finish the world update" is refused by it too.

A Centurion build kept without its world tables is not recorded here: T179's own
world-update record holds the tables it still needs (`ServersDownWork.keep`), and only an
import of them clears that one."""

REBUILD_OWED_REFUSAL = (
    "This server's image tags are mixed: an update or rebuild could not put the build from "
    "before it back, and left some images on one build and the rest on the other. It must be "
    "rebuilt before it can start: press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}."
)
"""Why no start is allowed while `START_REFUSED_FILE` is there."""


UNTESTED_BUILD = "untested"
"""`START_REFUSED_FILE`'s `why` when the tags name a new build that never started (T223)."""

UNTESTED_BUILD_REFUSAL = (
    "This server's image tags name a new build that has never started: an update or rebuild "
    "finished compiling it, and the build from before it could not be put back on its tags. "
    "A Start would run that untested build, so it must be rebuilt first: press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}."
)
"""Why no start is allowed while `START_REFUSED_FILE` says `untested` (T223, owner answer D1).

The record and the clearing are the mixed tags' own: only a Rebuild that succeeds removes
it, and the Rebuild press is not refused by it. Only the sentence differs, because "the
tags are mixed" is false here -- every one names the new build."""


def owed_start_refusal(server_dir: Path, *, rebuilding: bool = False) -> str | None:
    """Why no start may run here (`START_REFUSED_FILE`), or None. Never raises.

    `rebuilding` is the Rebuild press's own question: the rebuild it is about to run is
    the repair, so the record does not refuse it. A record that says `untested` refuses
    with `UNTESTED_BUILD_REFUSAL`; any other, or one nobody can read, as mixed tags.
    """
    if rebuilding:
        return None
    path = server_dir / START_REFUSED_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"{path} could not be read ({exc}); it refuses as mixed tags")
        return REBUILD_OWED_REFUSAL
    try:
        why = json.loads(text).get("why")
    except (ValueError, AttributeError):
        why = None
    return UNTESTED_BUILD_REFUSAL if why == UNTESTED_BUILD else REBUILD_OWED_REFUSAL


def owe_start(server_dir: Path, *, why: str = "rebuild") -> str:
    """Write `START_REFUSED_FILE`. Returns a warning sentence, or "" once written."""
    path = server_dir / START_REFUSED_FILE
    staged = path.with_name(path.name + ".yulon-new")
    try:
        staged.write_text(json.dumps({"version": 1, "why": why}) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError as also:
            logger.warning(f"could not remove {staged}: {also}")
        return (
            f"warning: {path} could not be written ({exc}), so nothing stops this server being "
            "started before it is rebuilt."
        )
    return ""


def forget_owed_start(server_dir: Path) -> str:
    """Remove `START_REFUSED_FILE`. Returns a warning sentence, or ""."""
    path = server_dir / START_REFUSED_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return f"warning: {path} could not be removed ({exc}); every start will go on refusing."
    return ""


class ServersLeftStopped(InstallerError, TrueAfterStop):
    """A rebuild's rollback put the old build back and did NOT start it (T179 final round).

    The update route's world tables could not all be put back for it, and no start
    is allowed until "Finish the world update" has run (`start_refusal()`). Its own
    type so the route's closing note says the server is stopped, not running.
    `TrueAfterStop` (T228): the log panel shows it after a Stop too.
    """


class RebuildChangedTheServer(InstallerError, TrueAfterStop):
    """A failed rebuild that had already changed what the server runs (T228 cold review).

    The new build replaced the containers with no rollback to put back, or the
    rollback put the old build back on a database the new build may have written
    to, running or not. Its sentence says which, and a Stop does not make it any
    less true, so the log panel shows it after a Stop too. A failure that changed
    nothing (a compile stopped before any container moved) stays a plain
    `InstallerError`, which after a Stop is a clean cancel.

    `up` False: the server is not up -- the new build never came up with nothing
    to put back, or the old build was put back and did not report ready either --
    so the update route's closing note must not say the folder agrees with what
    is running.
    """

    def __init__(self, *args: object, up: bool = True) -> None:
        super().__init__(*args)
        self.up = up


class CrashedAfterStop(InstallerError, TrueAfterStop):
    """A crash in the watch after the banner while a Stop was pending (`READY_CRASHED_AFTER_STOP`).

    NOT a `WorldStoppedAfterReadyError`, so `rebuild()` rolls back on it as on a
    crashed wait. A real failure, so not `StopTookEffect`; `TrueAfterStop`,
    because it says what the press does about the Stop, which is shown after one.
    """


class StoppedInTheWatch(ReadyWaitStopped):
    """`ReadyWaitStopped` inside the watch after the banner: the world had reported ready (T247).

    Its own type so the install says the world is up rather than loading
    (`INSTALL_LEFT_RUNNING`), in the log and in the Catalog's popup.
    """


class StoppedAndPutBack(RebuildChangedTheServer, PutBackAfterStop):
    """A Stop during the load, then a CLEAN rollback (T247 live review, the lead's ruling).

    The build from before is up again and its databases were put back (T217), so
    nothing the press did is left: the panel shows "Stopped:" with the sentence,
    not "Stopped. FAILED". A `RebuildChangedTheServer`, so every handler of that
    still runs.
    """


class _PutBackWhole(str):
    """`_restore_rollback()`'s sentence when the old build is up and its databases went back."""


class _NotUpEither(str):
    """`_restore_rollback()`'s sentence when the old build was put back and did not come up."""


class _LeftStopped(str):
    """`_restore_rollback()`'s sentence when it left the servers stopped (`ServersLeftStopped`)."""


class OldBuildNotStopped(InstallerError, TrueAfterStop):
    """The old build did not come up after a rollback, and its servers could not be stopped (T217).

    Its own type, NOT `ServersLeftStopped`: nothing here was stopped, so neither
    the press nor its closing note may say so (scoped re-review of c5bf1b67).
    `TrueAfterStop` (T228): the state it names outlives a Stop.
    """


class _NotStopped(str):
    """`_restore_rollback()`'s sentence for `OldBuildNotStopped`."""


class _DockerSilentForRestart(InstallerError):
    """A restore's recreate refused because Docker did not answer its wait (T223).

    Its servers were stopped before the tags moved back, so `_restore_rollback()`
    reports them left stopped rather than "did not report ready".
    """


class LeaveStopped(InstallerError):
    """Raised by a `ServersDownWork.back()`: the old build must NOT be started (T217).

    The update route's rollback could not put back something the old build would
    start on -- its copy of the databases the new build changed, or a source folder
    whose database updates the old build reads -- so `_restore_rollback()` leaves
    the servers stopped and says this sentence, which names the fix, instead of
    starting them.
    """


class _NotPutBack(str):
    """`_restore_rollback()`'s sentence when it stopped before the old build was back (T197).

    `rebuild()` raises it as `RollbackNotDone`, with whether any container was replaced
    (`touched`) and whether the tags were left mixed (`mixed`), which the update route reads.
    """

    touched: bool
    mixed: bool
    untested: bool
    """T223: every live tag names the new build, which has never started, because Docker
    did not answer for the restore. A start would run it, so `rebuild()` refuses one."""

    def __new__(
        cls, text: str, *, touched: bool, mixed: bool = False, untested: bool = False
    ) -> _NotPutBack:
        made = super().__new__(cls, text)
        made.touched = touched
        made.mixed = mixed
        made.untested = untested
        return made


@dataclass
class _Parking:
    """What `rebuild()` knows about the build it may keep, for `_restore_rollback()` (T224).

    `fingerprint` is F0: taken at the start of the build stage, after the recipe
    was rendered and before anything compiled, so it names the files the compile
    read. `made_unix` is when that build was made (a kept build that is used and
    kept again keeps its own date).
    """

    fingerprint: str | None = None
    after: str | None = None
    """F1: taken as the build stage ends, compose having tagged, so an edit made while
    it compiled shows -- and before a failure puts the recipe this press rendered back
    (T8), which would read as a change the compile never saw."""
    made_unix: int = 0
    tagging: bool = False
    """The kept build is being put on the live tags: from here a tag may have moved."""
    still_kept: bool = False
    """A kept build was being used and Docker refused a tag part-way: its names and
    record were not touched, so it is still kept and nothing new is."""


ROLLBACK_LEFT_RUNNING = (
    "Its containers are still up, but its world server is not ready, so nobody can log in. "
    "Press Stop on the Server tab to stop them."
)
"""T391: said last when a rollback's old build did not come up and was left running.

On m910q (2026-10-06) the world crash-looped under it while the header read
REALM ONLINE, and nothing in the failure said the containers were still up."""

ROLLBACK_LEFT_STOPPED_DATABASE = (
    "\nWhat the new build wrote into the database on its first start, if anything, is NOT put "
    "back by this, so the old build will start on the database as the new one left it."
)
"""The rollback's database sentence for servers it left stopped: "will start", not "is running"."""

SOURCES_PUT_BACK_STOPPED_NOTE = (
    "The source folders were put back on the commits they were on, so what is on disk is the "
    "build that was put back; it stays stopped until its world tables are in."
)
"""`SOURCES_PUT_BACK_NOTE` for a press whose rollback left the servers stopped (T179)."""

SOURCES_PUT_BACK_NOT_STOPPED_NOTE = (
    "The source folders were put back on the commits they were on, so what is on disk is the "
    "build that was put back."
)
"""`SOURCES_PUT_BACK_NOTE` when the old build did not come up and could not be stopped (T217)."""

SOURCES_PUT_BACK_NOT_UP_NOTE = (
    "The source folders were put back on the commits they were on, so what is on disk and the "
    "build that was put back agree again; the server is not up."
)
"""`SOURCES_PUT_BACK_NOTE` for a press whose put-back build did not report ready (T228)."""

SOURCES_PUT_BACK_NOTE = (
    "The source folders were put back on the commits they were on, so what is on disk and what "
    "your server is running agree again."
)
"""Appended to every failure after the first fetch. Says the invariant, not the intent.

It is appended rather than woven in because the sentence in front of it comes
from somewhere else entirely -- a patch's own refusal, a compiler, a `rebuild()`
rollback -- and the one thing all of them need to add is the same. What makes it
true is `_put_sources_back()`, which yields its own line per source, INCLUDING
when a restore failed: a user who sees that line and this sentence has the
contradiction in front of them rather than only the comfortable half.
"""

SOURCES_PUT_BACK_DATABASE_NOT_NOTE = (
    "The source folders were put back on the commits they were on, so what is on disk is the "
    "build that was put back; it stays stopped until its databases are restored."
)
"""`SOURCES_PUT_BACK_NOTE` when the update's copy of the databases could not go back, or went
back and the old build still did not come up (T217)."""


def _listed(names: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`: names as a sentence reads them."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _as_it_was(names: Sequence[str]) -> str:
    return "as it was" if len(names) == 1 else "as they were"


def _in_backups(paths: Sequence[Path]) -> str:
    """Files as the player finds them in the server folder: `backups/<file>`, joined."""
    return ", ".join(f"{path.parent.name}/{path.name}" for path in paths)


def _megabytes(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB"


def copy_opening_line(names: Sequence[str]) -> str:
    """Said right after the opening note, when the update copies databases (T217).

    Its clauses are in the order the rollback runs them: source folders, then
    this copy, then the build you have (`update_to_latest()`'s `back()`).
    """
    return (
        f"Just before the new build first starts, with your servers stopped, Yu'lon copies "
        f"{_listed(names)}, which it can change. If it does not come up, the source folders "
        f"go back first, then {'that copy' if len(names) == 1 else 'those copies'}, and only "
        "then does the build you have start again."
    )


def copy_taking_line(names: Sequence[str]) -> str:
    """Said before the update copies the databases its new build can change (T217)."""
    return (
        f"Copying {_listed(names)} with your servers stopped, right before the new build first "
        "starts, so it can be put back if that build does not come up."
    )


def copy_taken_line(copy: snapshot.Snapshot) -> str:
    """Said once the copy is on disk: what, how big, and the file the player would restore."""
    return (
        f"Copied {_listed(copy.databases)} ({_megabytes(copy.size_bytes)}) to "
        f"{_in_backups(copy.files)} before starting the new build."
    )


def copy_not_taken(names: Sequence[str], reason: str) -> str:
    """The failure sentence when the copy could not be taken: the new build was not started."""
    return (
        f"Yu'lon could not copy {_listed(names)} before starting the new build ({reason}), so it "
        "did not start it."
    )


COPY_NOT_TAKEN_DATABASE = (
    "Nothing in your databases was changed by the update: the new build never started."
)
"""The rollback's database sentence when the copy failed, so the new build never ran (T217)."""


def copy_putting_back_line(copy: snapshot.Snapshot) -> str:
    """Said as the copy goes back: a replacement, which is what the put-back does (cold review)."""
    return (
        f"Putting {_listed(copy.databases)} back {_as_it_was(copy.databases)} just before the "
        "new build started: the copy is checked, the tables the new build added are dropped, "
        "and the copy is loaded over the rest, before the build from before this update starts "
        "again."
    )


def copy_put_back_line(put: snapshot.PutBack) -> str:
    kept = f"; what the new build left is kept in {_in_backups(put.safety)}" if put.safety else ""
    return f"Put {_listed(put.restored)} back{kept}."


def copy_put_back_database(put: snapshot.PutBack, *, not_copied: Sequence[str] = ()) -> str:
    """The rollback's database sentence once the copy went back (T217).

    Replaces "What the new build wrote into the database ... is NOT put back". The
    order it states is the order `update_to_latest()` runs: tags, then source
    folders, then this copy, then the old build. "Nobody played on it" holds
    because a build is rolled back only before its ready banner; a build that
    stops after it is KEPT (`WorldStoppedAfterReadyError`, T71), and its copy is
    not put back.
    """
    names = put.restored
    kept = (
        f"; what it left in {'that database' if len(names) == 1 else 'those databases'} is kept "
        f"in {_in_backups(put.safety)}"
        if put.safety
        else ""
    )
    said = (
        f"Its source folders were put back first, then {_listed(names)} were replaced with the "
        "copy taken just before the new build started: the tables the new build added were "
        "dropped and the copy was loaded over the rest, so the old build starts on the "
        f"databases it knows. The new build never reported ready, so nobody played on it{kept}."
    )
    if not_copied:
        said += (
            f" {_listed(not_copied)} {'was' if len(not_copied) == 1 else 'were'} not copied, so "
            "what the new build's updater wrote into "
            f"{'it' if len(not_copied) == 1 else 'them'}, if anything, is NOT put back."
        )
    return said


def copy_back_with_the_sources(put: snapshot.PutBack) -> str:
    """Mixed tags (T197): the sources went back, and the copy followed them (T217)."""
    names = put.restored
    verb = "was" if len(names) == 1 else "were"
    return (
        f"{_listed(names)} {verb} put back {_as_it_was(names)} just before the new build "
        "started, after the source folders."
    )


def copy_not_put_back(copy: snapshot.Snapshot, reason: str) -> str:
    """Why the old build was left stopped: its database copy would not go back (T217)."""
    names = copy.databases
    return (
        f"{_listed(names)} could not be put back {_as_it_was(names)} just before the new build "
        f"started ({reason}), so the old build was not started on the database the new one "
        f"changed. Open Maintenance, choose {_in_backups(copy.files)} and press Restore (it "
        "works with the server stopped), then press Start."
    )


def copy_not_usable(copy: snapshot.Snapshot, reason: str) -> str:
    """The old build was left stopped: the copy failed its check, and nothing was touched."""
    names = copy.databases
    return (
        f"{_listed(names)} could not be put back {_as_it_was(names)} just before the new build "
        f"started: {reason}. So the old build was not started, and "
        f"{'that database is' if len(names) == 1 else 'those databases are'} as the new build "
        f"left {'it' if len(names) == 1 else 'them'}. The copy ({_in_backups(copy.files)}) "
        "cannot be restored either. If you have a backup of your own from before this update, "
        "restore it on Maintenance (it works with the server stopped), then press Start."
    )


def copy_old_build_down(
    copy: snapshot.Snapshot,
    put: snapshot.PutBack,
    *,
    older: Sequence[Path] = (),
    not_stopped: str | None = None,
) -> str:
    """The old build did not come up on the databases put back (T217 live proof, item 5).

    "Stopped" only when `not_stopped` is None, i.e. the stop went through; else it
    says why it did not and sends the player to Stop (scoped re-review). `older`
    are the copies earlier updates took, newest first, named only when there are
    any -- with the next older one as the way back when the newest does not do it:
    a world restarted during the update can change its databases before the
    newest copy is taken (the re-live of 2026-10-05, item 5).
    """
    names = put.restored
    left = f", as the new build left them in {_in_backups(put.safety)}" if put.safety else ""
    earlier = (
        " Copies earlier updates took before their new build started are kept too, newest "
        f"first: {_in_backups(older)}. If Restore of the newest copy does not bring the old "
        "build up, restore the next older copy listed."
        if older
        else ""
    )
    head = (
        f"Its source folders and {_listed(names)} were put back, and the build from before "
        "this update still did not come up"
    )
    kept = (
        f"Every copy is kept: {_listed(copy.databases)} as they were just before the new build "
        f"started are in {_in_backups(copy.files)}{left}.{earlier}"
    )
    if not_stopped is not None:
        return (
            f"{head}. Yu'lon could not stop its servers ({not_stopped}), so they may still be "
            f"restarting: press Stop on the Server tab. {kept} Once it is stopped, restore the "
            "one you want on Maintenance, then press Start."
        )
    return (
        f"{head}, so its servers were stopped. {kept} Restore the one you want on Maintenance "
        "(it works with the server stopped), then press Start."
    )


def copy_kept_note(copy: snapshot.Snapshot) -> str:
    """Added when the new build stays: its copy was not needed (T217, T197's exits)."""
    names = copy.databases
    return (
        f"The copy of {_listed(names)} taken before it started is kept in "
        f"{_in_backups(copy.files)}; it was not needed, because the new build is what runs."
    )


SOURCES_NOT_ALL_BACK_NOTE = (
    "Not every source folder went back to the commit it was on -- the line above names it, "
    "and Start says how to put it back -- so what is on disk and the build your server has do "
    "NOT agree until it is back."
)
"""`SOURCES_PUT_BACK_NOTE` when a source would not go back (T217): never "agree again"."""


def _sources_note(failed: Sequence[object]) -> str:
    """The closing note once the sources were asked back: "agree again" only if all went."""
    return SOURCES_NOT_ALL_BACK_NOTE if failed else SOURCES_PUT_BACK_NOTE


def source_not_back(repo: str, dest: Path, old: str, reason: str) -> str:
    """Why the old build was left stopped: a source folder would not go back (T217).

    The old WotLK worldserver reads its module's database updates from the
    folder, not from its image, so starting it on a folder that still holds the
    new module migrates the database again.
    """
    return (
        f"{repo} in {dest} could not be put back on {old[:7]} ({reason}); that folder still "
        "holds the new code, and the old build reads its database updates from it. Put it back "
        f"with `git -C {dest} checkout --detach --force {old}`, then press Start."
    )


SOURCES_OFF_FILE = ".yulon-sources-off.json"
"""A rollback could not put a source folder back on the commit the running build came from.

Written by the update route beside the install record (T217), read by every
start (`Controller.refuse_start()`), which REFUSES while a folder it names is
off its commit (the owner's decision on T217 (a), 2026-10-05; it warned until
then), as Rebuild does. Forgotten once
every folder it names is back on its commit, or by a Rebuild or update that
succeeds. `{"version": 1, "sources": [{"repo", "dest", "commit"}]}`.
"""


def remember_sources_off(server_dir: Path, rows: Sequence[tuple[str, Path, str]]) -> str:
    """Write `SOURCES_OFF_FILE` for `(repo, folder, commit)` rows. "" once written, else why not."""
    path = server_dir / SOURCES_OFF_FILE
    staged = path.with_name(path.name + ".yulon-new")
    record = {
        "version": 1,
        "sources": [
            {"repo": repo, "dest": str(dest), "commit": commit} for repo, dest, commit in rows
        ],
    }
    try:
        staged.write_text(json.dumps(record) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError as also:
            logger.warning(f"could not remove {staged}: {also}")
        return f"warning: {path} could not be written ({exc}), so Start will not warn about it."
    return ""


def forget_sources_off(server_dir: Path) -> None:
    """Remove `SOURCES_OFF_FILE`; a failure is logged, never raised."""
    path = server_dir / SOURCES_OFF_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not remove {path}: {exc}")


def source_off_its_build(dest: Path, head: str, built: str) -> str:
    """Rebuild's refusal of a folder off the commit its build came from (T217): one sentence.

    Said by the press (`_refuse_sources_off_their_build()`) and, before its
    question, by the view (`rebuild_refusal_before_asking()`).
    """
    update = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
    return (
        f"{dest} is on {head[:7]}, but this server was built from {built[:7]}, so rebuilding "
        f"it now would compile a mix of the two. Nothing was changed. Press {update} to move "
        "every source together, or put the folder back with this:\n"
        f"git -C {dest} checkout --detach --force {built}"
    )


PARKED_BUILD_FILE = ".yulon-parked-build.json"
"""The record of a kept build (T224): its image ids and the fingerprint of what it was made from.

Written beside the install record by a rebuild that keeps its finished build
(`PARKED_TAG_SUFFIX`), and only after the `-parked` names are on the daemon, so
a crash part-way leaves names without a record -- never a record naming a build
that is not there. `{"version": 1, "fingerprint": "<64 hex>", "images": {"<ref>":
"<id>"}, "made_unix": <int>, "app": "<version>"}`. Read by the next rebuild's
build stage, which uses the build only when the fingerprint taken then and every
image id match; by the Rebuild confirmation and the Server tab, which read only
this file. Forgotten when the build is used, when it no longer matches, and by
"Remove kept build…". Its name starts `.yulon`, which `build_context` leaves out
of the fingerprint, so writing it changes nothing it records.
"""

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ParkedBuild:
    """One `PARKED_BUILD_FILE` record (T224)."""

    fingerprint: str
    images: Mapping[str, str]
    made_unix: int
    app: str

    def when(self) -> str:
        """When the kept build was made, in this computer's time, for a sentence."""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.made_unix))


def read_parked_build(server_dir: Path) -> ParkedBuild | None:
    """The record of a kept build in `server_dir`, or None: absent, unreadable or not one.

    Strict: anything this version would not have written is no record, and a
    build with no record is never used (the next rebuild removes its names).
    """
    path = server_dir / PARKED_BUILD_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} is not a kept build's record: {exc}")
        return None
    images = raw.get("images") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or not isinstance(raw.get("fingerprint"), str)
        or not _FINGERPRINT.match(raw["fingerprint"])
        or not isinstance(images, dict)
        or not images
        or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in images.items())
        or not isinstance(raw.get("made_unix"), int)
        or isinstance(raw.get("made_unix"), bool)
        or not isinstance(raw.get("app"), str)
    ):
        logger.warning(f"{path} is not a kept build's record this version reads")
        return None
    return ParkedBuild(raw["fingerprint"], dict(images), raw["made_unix"], raw["app"])


def remember_parked_build(server_dir: Path, record: ParkedBuild) -> str:
    """Write `PARKED_BUILD_FILE`, whole or not at all. "" once written, else why not."""
    path = server_dir / PARKED_BUILD_FILE
    staged = path.with_name(path.name + ".yulon-new")
    body = {
        "version": 1,
        "fingerprint": record.fingerprint,
        "images": dict(record.images),
        "made_unix": record.made_unix,
        "app": record.app,
    }
    try:
        staged.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError as also:
            logger.warning(f"could not remove {staged}: {also}")
        return f"its record {path} could not be written ({exc})"
    return ""


def forget_parked_build(server_dir: Path) -> str:
    """Remove `PARKED_BUILD_FILE`. "" once it is gone (or was never there), else why not."""
    path = server_dir / PARKED_BUILD_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not remove {path}: {exc}")
        return f"the record {path} could not be removed ({exc})"
    return ""


STOPPED_BUILD_FILE = ".yulon-stopped-build.json"
"""A rebuild was stopped mid-compile, and Docker may still finish that build (T225).

Measured on a Windows 11 test machine, 2026-10-05: Stop pressed during the compile, the live
tag still on the old image two minutes later -- and about 13 minutes after the
Stop BuildKit exported a new image and moved the live tag onto it. So a press
whose compile was cancelled or abandoned keeps its `-rollback` names and writes
this record (`{"version": 1, "refs": [...], "fingerprint": "<64 hex>" | null,
"made_unix": <int>}`); every Start compares each live tag with its `-rollback`
name while it is there (`stopped_build_refusal()`), and the next Rebuild, Update
or Return settles it (`_settle_stopped_build()`). Uninstall removes it.
"""

STOPPED_BUILD_NOTE = (
    "Docker may still finish that build in the background and move this server's image tags "
    "onto it. The build you have now is kept under a second name in case it does, and every "
    "Start checks: if the tags moved, Start is refused until you press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}."
)
"""Said after the failure of a press whose compile was stopped (T225, live finding)."""

STOPPED_BUILD_LANDED_REFUSAL = (
    "A rebuild that was stopped kept compiling in Docker's background and finished afterwards, "
    "so this server's image tags now name that new build, which has never started. A Start would "
    "run it, so it is refused. Press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}: it puts the tags "
    "back on the build this server ran, and can use the finished build instead of compiling if "
    "the server folder has not changed since."
)
"""Why no start may run once a stopped build has moved a live tag (T225, live finding)."""


@dataclass(frozen=True)
class StoppedBuild:
    """One `STOPPED_BUILD_FILE` record (T225)."""

    refs: tuple[str, ...]
    fingerprint: str | None
    made_unix: int


def read_stopped_build(server_dir: Path) -> StoppedBuild | None:
    """The record of a stopped build in `server_dir`, or None: absent, unreadable or not one."""
    path = server_dir / STOPPED_BUILD_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} is not a stopped build's record: {exc}")
        return None
    refs = raw.get("refs") if isinstance(raw, dict) else None
    fingerprint = raw.get("fingerprint") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or not isinstance(refs, list)
        or not refs
        or not all(isinstance(ref, str) and ref for ref in refs)
        or not (
            fingerprint is None
            or (isinstance(fingerprint, str) and _FINGERPRINT.match(fingerprint))
        )
        or not isinstance(raw.get("made_unix"), int)
        or isinstance(raw.get("made_unix"), bool)
    ):
        logger.warning(f"{path} is not a stopped build's record this version reads")
        return None
    return StoppedBuild(tuple(refs), fingerprint, raw["made_unix"])


def remember_stopped_build(server_dir: Path, record: StoppedBuild) -> str:
    """Write `STOPPED_BUILD_FILE`, whole or not at all. "" once written, else why not."""
    path = server_dir / STOPPED_BUILD_FILE
    staged = path.with_name(path.name + ".yulon-new")
    body = {
        "version": 1,
        "refs": list(record.refs),
        "fingerprint": record.fingerprint,
        "made_unix": record.made_unix,
    }
    try:
        staged.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError as also:
            logger.warning(f"could not remove {staged}: {also}")
        return f"its record {path} could not be written ({exc})"
    return ""


def forget_stopped_build(server_dir: Path) -> str:
    """Remove `STOPPED_BUILD_FILE`. "" once it is gone (or was never there), else why not."""
    path = server_dir / STOPPED_BUILD_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not remove {path}: {exc}")
        return f"the record {path} could not be removed ({exc})"
    return ""


def stopped_build_refusal(server_dir: Path, image_id: Callable[[str], str | None]) -> str | None:
    """Why a stopped build refuses a start, or None (T225; the lead, scoped re-review).

    Docker is asked only while `STOPPED_BUILD_FILE` is there, so a server with
    none never waits on it. Per ref: both it and its `-rollback` name answer and
    differ -- the build landed (`STOPPED_BUILD_LANDED_REFUSAL`); exactly one of
    the two answers (a lookup that timed out, a `-rollback` name deleted by hand)
    -- the check could not be completed, which refuses too
    (`STOPPED_BUILD_UNCHECKED_REFUSAL`); neither answers -- Docker is silent, and
    a Start fails on its own then, so that alone refuses nothing. Never raises.
    """
    record = read_stopped_build(server_dir)
    if record is None:
        return None

    def ask(ref: str) -> str | None:
        try:
            return image_id(ref)
        except Exception as exc:  # noqa: BLE001 - a question, never a new failure
            logger.warning(f"could not read the image {ref} names: {exc}")
            return None

    unchecked = False
    for ref in record.refs:
        now = ask(ref)
        before = ask(ref + ROLLBACK_TAG_SUFFIX)
        if now is not None and before is not None:
            if now != before:
                return STOPPED_BUILD_LANDED_REFUSAL
        elif now is not None or before is not None:
            unchecked = True
    return STOPPED_BUILD_UNCHECKED_REFUSAL if unchecked else None


STOPPED_EARLIER_NOTE = (
    "A rebuild stopped earlier may still finish in Docker's background, so the build you have "
    "now stays kept under a second name, and every Start still checks whether the tags moved."
)
"""Said after a press that failed while a stopped build's record was there (T225)."""

STOPPED_BUILD_UNCHECKED_REFUSAL = (
    "Yu'lon could not check whether a rebuild that was stopped finished in Docker's background, "
    "so Start waits: if it did, a Start would run a build that never started. Press "
    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}, which settles it."
)
"""Why no start may run while Docker half-answers the stopped-build check (T225, lead)."""


def folder_start_refusal(
    server_dir: Path, image_id: Callable[[str], str | None], *, rebuilding: bool = False
) -> str | None:
    """Every refusal of a start that the server folder records, or None (T197, T223, T225).

    `START_REFUSED_FILE` first, then a stopped build that landed or could not be
    checked. A Rebuild (`rebuilding`) is the way out of both, so neither refuses it.
    The one call every start path makes: the controller's `refuse_start()`, the
    engine's `start_refusal()`, the Tortoise bot rebuild's pre-check and the
    Centurion finish.
    """
    if rebuilding:
        return None
    return owed_start_refusal(server_dir) or stopped_build_refusal(server_dir, image_id)


REMOVE_KEPT_BUILD_LABEL = "Remove kept build…"
"""The Server tab's press that removes a kept build now (T224, owner D3)."""

REMOVE_KEPT_BUILD_QUESTION = (
    "Remove the kept build? It frees the disk space it takes. The server you have now is not "
    "touched, and Start never used the kept build. The next rebuild then compiles the server "
    "instead of using it."
)


def parked_build_note(server_dir: Path) -> str | None:
    """The Server tab's line while a build is kept (T224, D3), from the record alone; else None."""
    record = read_parked_build(server_dir)
    if record is None:
        return None
    return (
        f"A finished build from {record.when()} is kept for the next rebuild. Start does not use "
        f"it. A rebuild uses it if the server folder has not changed since, and removes it "
        f"otherwise. {REMOVE_KEPT_BUILD_LABEL} removes it now."
    )


@dataclass(frozen=True)
class KeptBuildRoute:
    """The two halves of the Server tab's kept-build banner (T224, D3), wired for one install.

    `check` reads only `PARKED_BUILD_FILE` and never raises: the note, or None.
    `remove` lets the `-parked` names go and forgets the record, and returns the
    sentence that says what it did.
    """

    check: Callable[[], str | None]
    remove: Callable[[], str]


@dataclass(frozen=True)
class SourceOff:
    """One folder `SOURCES_OFF_FILE` names that is still off the commit its build came from."""

    repo: str
    dest: Path
    built: str
    """The full commit the server's build was made from, which the folder should be on."""
    head: str | None
    """What `.git/HEAD` says the folder is on now; None when it cannot be read as a commit."""


def sources_still_off(server_dir: Path) -> tuple[SourceOff, ...]:
    """The folders `SOURCES_OFF_FILE` names that are still off their build (T217). Never raises.

    Reads `.git/HEAD` (`read_head_file()`), no git run. Once none is left the
    record is forgotten. The one reading behind the start refusal and the
    Modules tab's version line and "Return to the tested pin…", so the two
    cannot disagree about whether a folder is off.
    """
    path = server_dir / SOURCES_OFF_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except (OSError, ValueError) as exc:
        logger.warning(f"{path} could not be read ({exc}); not reading it")
        return ()
    rows = raw.get("sources") if isinstance(raw, dict) else None
    off: list[SourceOff] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        dest, commit = Path(str(row.get("dest", ""))), str(row.get("commit", ""))
        head = read_head_file(dest)
        if not commit or head == commit:
            continue
        off.append(SourceOff(str(row.get("repo", dest.name)), dest, commit, head))
    if not off:
        forget_sources_off(server_dir)
    return tuple(off)


def _on_now(row: SourceOff) -> str:
    return (
        f"is on {row.head[:7]}, not on {row.built[:7]}"
        if row.head
        else (f"is not on {row.built[:7]}")
    )


def sources_off_refusal(server_dir: Path) -> str | None:
    """Why no start may run while `SOURCES_OFF_FILE` names a folder still off its commit; else None.

    The owner's decision on T217 (a), 2026-10-05: Start REFUSES (it warned until
    then). The live proof showed the warned start's old world server apply the
    database updates it found in the off-commit module folder and crash-loop.
    Read by `Controller.refuse_start()`, so Start, Start and play, the launcher's
    PLAY, Restart and Recreate all refuse; NOT by the engine's `start_refusal()`,
    because "Return to the tested pin…" is one of the two ways out it names, and
    the Modules tab offers it while this refuses (`source_version()`).

    Each `git` command is on a line of its own (the owner's T296 rule: a command
    the player types stays, on its own line). Never raises.
    """
    off = sources_still_off(server_dir)
    if not off:
        return None
    named = "; ".join(f"{row.repo} in {row.dest} {_on_now(row)}" for row in off)
    commands = "\n".join(f"git -C {row.dest} checkout --detach --force {row.built}" for row in off)
    back = server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)
    rebuild = server_build_presses.under_server_build(server_build_presses.REBUILD)
    one = len(off) == 1
    return (
        f"{named}, the commit this server was built from, and its world server would apply the "
        f"database updates in {'that folder' if one else 'those folders'}, so the server is not "
        f"started: press {back}, or put the {'folder' if one else 'folders'} back with "
        f"{'this command' if one else 'these commands'} and press {rebuild}:\n{commands}"
    )


@dataclass
class _UpdateCopy:
    """One update press's copy of the databases its new build can change (T217)."""

    names: tuple[str, ...]
    not_copied: tuple[str, ...] = ()
    taken: snapshot.Snapshot | None = None
    put: snapshot.PutBack | None = None
    take_failed: bool = False
    put_failed: bool = False
    old_build_down: bool = False


REPAIR_FILES_LABEL = "Repair server files…"
"""The Server tab's T106/T137 press, here because the engine's own sentences name it (T170).

`controller_view` draws the button from this string, and `_restore_the_folder()`
sends a player to it: one spelling, so a rename moves both."""

RECREATE_CONTAINERS_LABEL = "Recreate containers…"
"""The Tuning tab's press that the Repair banner offers next (T170), for the same reason."""


def compose_back_advice(server_dir: Path, *, once_fixed: bool = True) -> str:
    """The press that puts Yu'lon's compose file back over the repository's own (T170).

    Said when "Update the server to latest…" put a WotLK checkout back on its
    old commit and could not write Yu'lon's `docker-compose.yml` into it
    again: `checkout --force` has left the repository's own file there, tracked
    and unmodified. Rebuild refuses that folder (`_refuse_unless_rebuildable()`)
    and so does the same Update press, which starts with that guard. Repair
    server files does not, since T170: it replaces exactly that file, by the
    install's own `replaceable` rule (`_upstream_compose_facts()`), and then
    offers Recreate as it always does.

    **Where the press is depends on where the server lives.** Yu'lon on
    Windows offers no Repair for a server inside a WSL distro
    (`install_wiring.repair_compose_for_app()` answers None there: its seams
    render for this host, and the folder's id and SELinux answers are the
    distro's). The Yu'lon inside the distro that built the server renders for
    it, on the folder's Linux path, and its Server tab offers the Repair.

    `once_fixed` is False where nothing failed to be fixed first: Rebuild's own
    refusal of such a folder (`_refuse_unless_rebuildable()`).
    """
    press = f"press \u201c{REPAIR_FILES_LABEL}\u201d on"
    when = "Once the reason is fixed, " if once_fixed else ""
    then = (
        ": it writes Yu'lon's file over the repository's untouched one and keeps that one beside "
        f"it as a backup. Then press \u201c{RECREATE_CONTAINERS_LABEL}\u201d, which it offers "
        f"next; {server_build_presses.under_server_build(server_build_presses.REBUILD)} works "
        "again from then on."
    )
    found = platform.wsl_location(server_dir)
    if found is None:
        return f"{when or 'To mend it, '}{press} the Server tab{then}"
    distro, inside = found
    return (
        f"Yu'lon on Windows cannot write Yu'lon's file there: it rebuilds and updates a server "
        f"inside the WSL distro {distro}, but it does not rewrite its compose files. {when}"
        f"{'o' if when else 'O'}pen this server ({inside}) in the Yu'lon inside {distro} that "
        f"built it and {press} its Server tab{then}"
    )


_DB_REPO_SUFFIX = "-db"
"""How a world-database repository is spelled, in upstream CMaNGOS's own convention.

`tbc-db`, `classic-db`, `wotlk-db`, `cata-db`. See
`StagedInstaller.sources_that_move()` for why this is a suffix rule and what it
costs.
"""


def held_at_its_pin(source: EmulatorSource) -> bool:
    """Does this source stay on its tested commit when the server is updated to latest?

    A module function rather than a method, so `test_catalog` can enumerate the
    answer for every shipped source without building an engine -- which is what
    keeps the suffix rule above from being a rule nobody checked.
    """
    name = source.repo.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name.endswith(_DB_REPO_SUFFIX)


def server_source_folders(entry: CatalogEntry) -> frozenset[PurePosixPath]:
    """Every folder under the server dir this entry's install clones a source into (T554).

    The core itself (`dest: "."`) is not a folder of its own. Unlike `server_update_dests()`
    a source held at its pin counts: the Modules tab must not offer a module whose clone
    folder the server build already owns, whether or not "Update the server to latest…"
    moves it. Normalised through `PurePosixPath`.
    """
    return frozenset(
        path
        for path in (PurePosixPath(source.dest) for source in entry.emulator.sources)
        if path != PurePosixPath(".")
    )


def server_update_dests(entry: CatalogEntry) -> frozenset[PurePosixPath]:
    """Where "Update the server to latest…" moves a checkout on this entry, by `dest` (T146).

    The moving sources are `StagedInstaller.sources_that_move()`'s, read through
    the same `held_at_its_pin()`, so a `*-db` source is never here. The Modules
    tab asks this of a folder no manifest names: on WotLK `modules/mod-playerbots`
    is one, cloned by the SERVER install, and this button is what moves it.
    Normalised through `PurePosixPath`, so a `dest` spelled `./modules/x/` still
    matches the folder it names.
    """
    return frozenset(
        PurePosixPath(source.dest)
        for source in entry.emulator.sources
        if not held_at_its_pin(source)
    )


def _named(paths: Sequence[str], most: int = 5) -> str:
    """A refusal's file list: the first few, and how many more there are.

    Capped because the list goes into a modal dialog: a user who ran `make` in
    their server source has thousands of changed files, and a message box
    holding all of them has no readable sentence in it at all.
    """
    if len(paths) <= most:
        return ", ".join(paths)
    return f"{', '.join(paths[:most])} and {len(paths) - most} more"


def _by_repo(rev: SourceRev) -> str:
    """Sort key for `source_revs`, so the record's order does not follow the catalog's."""
    return rev.repo


def _commits(count: int) -> str:
    return f"{count} commit" if count == 1 else f"{count} commits"


def rewritten_line(repo: str, tag: str, count: int) -> str:
    """The extra line the update question carries for a release on rewritten history (T126).

    The lead's words, with the repository in front so a two-source question
    says which one. Said in the confirmation BEFORE the answer and again in the
    press's output, which is the record that it happened.
    """
    return (
        f"{repo}: Upstream rewrote its history: the release {tag} does not contain "
        f"{_commits(count)} this server was built from. Updating moves the server onto the "
        f"release as upstream publishes it."
    )


class RewrittenHistory(InstallerError):
    """A release on rewritten history that the answered question did not describe (T126).

    Carries the line, so the route can put it in front of the player the next
    time it asks. Raised before any source moves.
    """

    def __init__(self, repo: str, line: str) -> None:
        super().__init__(
            f"{line} The question you answered did not say so, so nothing was changed. "
            # T89: the press is an entry in the "Server build ▾" menu now, and
            # since T155 the sentence is built from the label it names.
            "Press "
            f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)} "
            "again: it will ask with that line in it."
        )
        self.repo = repo
        self.line = line


@dataclass(frozen=True)
class ReleaseTarget:
    """Where an update moves a source that follows its releases, and what it says (T126)."""

    tag: str
    rev: str
    """The release's commit, or the checkout's own HEAD when that already contains it."""
    line: str


@dataclass(frozen=True)
class SourceRev:
    """Where ONE source of this install stands against the commit the gates ran on (T64).

    Written only by `StagedInstaller.update_to_latest()` -- an install leaves
    this empty, because an install puts every source exactly on its pin and a
    record saying so would be a record of the catalog rather than of the folder.

    Three facts and no fourth, which is the shape the approved design names:

    * `built` is what the checkout is AT, in `git.head_version()`'s spelling
      (`a1b2c3d · 2026-09-16`) rather than a bare sha. The version line needs
      the date as well as the abbreviation, and reusing the one string this app
      already formats for exactly that purpose is cheaper than a fourth key and
      cannot drift from the modules panel beside it.
    * `pin` is `EmulatorSource.rev` as the catalog spelled it at the moment of
      the update -- the full 40-character sha. Recorded rather than read back
      off the catalog when the line is drawn, because the two are different
      questions: "what was this measured against" is a fact about the press,
      and a later app version that moves the pin must not be able to rewrite
      what an earlier press did.
    * `ahead` is how many commits `built` carries that `pin` does not, or
      `None` for "could not count". `None` and `0` are deliberately different:
      zero is "you are on the tested pin" and `None` is "nobody could say",
      and collapsing them is the defect `read_claim()` and `git.BehindReader`
      each record once already.
    """

    repo: str
    built: str
    pin: str = ""
    ahead: int | None = None
    release: str = ""
    """The release tag this source was moved to, for a source that follows releases (T126).

    Empty for every branch-following source and for a return to the pin: the
    pin is a commit the gates ran on, not a release, and naming the release
    the pin happens to sit near would be a guess.
    """


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
    error_run: str = ""
    """Which kind of run wrote `last_error` (T207): `ERROR_RUN_INSTALL` or `ERROR_RUN_REBUILD`.

    T206's import stage stops a world the previous run left running only after a
    failed INSTALL: a remembered server whose Rebuild, update or repair press
    failed records `last_error` too, and its running world is somebody's server.
    Empty in files written before T207, which is read as "not provably an
    install" -- refused, never stopped. ADDITIVE, for `source_revs`' reason.
    """
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

    source_revs: tuple[SourceRev, ...] = ()
    """Where each source stands against its tested pin, after an update to latest (T64).

    Empty on every install and on every state file written before T64, which
    is the one value that means "this folder is on the catalog's pins" -- the
    version line then says nothing rather than inventing a reading.

    ADDITIVE, so `version` stays 1: an older build reads the file, does not
    know this key, and `write_state()` would drop it. That is the lossy
    downgrade `unknown` exists to prevent for stage names, and it is NOT
    prevented here on purpose -- the cost of losing it is a version line that
    stops being drawn until the next update press, and the cost of carrying an
    uninterpretable copy of it would be an older build reporting a reading it
    cannot check against the catalog it ships. A hint that goes missing is the
    right failure for a hint.

    A tuple rather than the `dict` the file holds, because `InstallState` is
    frozen and a mapping field would make it unhashable and comparable by
    something mutable. `rev_for()` is the lookup.
    """

    refused_updates: tuple[tuple[str, str], ...] = ()
    """`(repo, commit)`: an upstream commit "Update the server to latest…" refused (T179).

    Written when a family refuses what an update would bring in a way no second
    press can change (`UpdateRefused`: TrinityCore's characters- or accounts-database
    change), so the Server tab stops offering that same commit
    (`upstream_news()`); upstream moving past it offers again. Cleared for a repo
    by an update that lands. ADDITIVE, for `source_revs`' reason.
    """

    def rev_for(self, repo: str) -> SourceRev | None:
        """This install's record for `repo`, or None if it has none."""
        return next((rev for rev in self.source_revs if rev.repo == repo), None)

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
        return replace(
            self, completed=tuple(s for s in order if s in done), last_error="", error_run=""
        )

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
    run = parsed.get("error_run")
    updated = parsed.get("updated_unix")
    version = parsed.get("version")
    return InstallState(
        game_id=game_id,
        install_id=install_id,
        family=family if isinstance(family, str) else "",
        completed=stages,
        last_error=error if isinstance(error, str) else "",
        error_run=run if isinstance(run, str) and isinstance(error, str) and error else "",
        updated_unix=updated if isinstance(updated, int) else 0,
        version=version if isinstance(version, int) else STATE_VERSION,
        unknown=unknown,
        source_revs=_parse_source_revs(parsed.get("source_revs"), path),
        refused_updates=_parse_refused(parsed.get("refused_updates")),
    )


def _refused_for(record: InstallState | None, source: EmulatorSource) -> str:
    """The commit an update of `source` refused, for a branch-following source; else ""."""
    if record is None or source.follow == "releases":
        return ""
    return next((sha for repo, sha in record.refused_updates if repo == source.repo), "")


def _parse_refused(raw: object) -> tuple[tuple[str, str], ...]:
    """`refused_updates` as `(repo, commit)` pairs; anything else is no record (T179)."""
    if not isinstance(raw, dict):
        return ()
    return tuple(
        sorted(
            (repo, sha)
            for repo, sha in raw.items()
            if isinstance(repo, str) and isinstance(sha, str) and sha
        )
    )


def _parse_source_revs(raw: object, path: Path) -> tuple[SourceRev, ...]:
    """The `source_revs` mapping as records, dropping anything that is not one (T64).

    Tolerant in exactly the direction the rest of this parser is: a key that is
    not a mapping, or a record with no `built`, is DROPPED rather than raised
    on, because the whole file is a hint and a damaged hint about a version
    line must not make an install unopenable. What is not tolerated is a
    plausible-looking wrong value -- an `ahead` that is not an `int` becomes
    `None` ("could not say") and never `0` ("on the pin").

    Sorted by repo so the file is stable between writes: `json.dumps` preserves
    insertion order, and a record whose ordering followed the catalog's would
    produce a different file every time the catalog was reordered, which reads
    as a change to anything watching the folder.
    """
    if not isinstance(raw, dict):
        if raw is not None:
            logger.debug(f"{path} has a source_revs that is not a mapping; ignoring it")
        return ()
    found: list[SourceRev] = []
    for repo, record in raw.items():
        if not isinstance(repo, str) or not isinstance(record, dict):
            continue
        built = record.get("built")
        if not isinstance(built, str) or not built:
            continue
        pin = record.get("pin")
        ahead = record.get("ahead")
        release = record.get("release")
        found.append(
            SourceRev(
                repo=repo,
                built=built,
                pin=pin if isinstance(pin, str) else "",
                # `bool` is an `int` in Python and `True` would arrive as 1
                # commit past the pin -- a number on somebody's screen with
                # nothing behind it.
                ahead=ahead if isinstance(ahead, int) and not isinstance(ahead, bool) else None,
                release=release if isinstance(release, str) else "",
            )
        )
    return tuple(sorted(found, key=lambda rev: rev.repo))


def _rev_record(rev: SourceRev) -> dict[str, object]:
    """One `source_revs` row as the file holds it. `release` only when there is one (T126)."""
    record: dict[str, object] = {"built": rev.built, "pin": rev.pin, "ahead": rev.ahead}
    if rev.release:
        record["release"] = rev.release
    return record


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
    if state.source_revs:
        # Only when there is one, so the file an ordinary install writes is byte
        # for byte what it was before T64: an empty mapping in every state file
        # in existence would be a change to a file other things read, made for a
        # feature most installs will never press.
        payload["source_revs"] = {rev.repo: _rev_record(rev) for rev in state.source_revs}
    if state.refused_updates:
        payload["refused_updates"] = dict(state.refused_updates)
    if state.last_error and state.error_run:
        # T207: only beside a failure, so a file with none is byte for byte as before.
        payload["error_run"] = state.error_run
    tmp = path.with_name(path.name + ".new")
    try:
        server_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)  # atomic on POSIX and on Windows
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.warning(f"could not record install progress in {path}: {exc}")


BUILD_CACHE_FILE = ".yulon-build-cache.json"
"""How much build cache Docker held as this install's unfinished build first started (T203).

Preflight credits a resumed build with the cache it ADDED since, never with the
whole machine's: Yu'lon never prunes, so the cache on a machine with two servers
is mostly the other one's, and crediting it let a second server's resume pass
the floor on space the first server's database volume needs (review,
2026-10-04). A file of its own and not a key in `STATE_FILE`, because the spine
writes the record from its own copy at every stage end and on failure
(`_run_one()`, `_record_error()`), so a key a stage wrote mid-way would be gone
by the next write.

`finished` is what makes the figure the FIRST attempt's. A build that starts
while the file says an earlier one never finished keeps the lower of the two
figures (`build_cache_baseline_for()`): writing the new one over it credited a
second failed build with nothing, because its starting figure already held the
first one's compile (review of PR 294, 2026-10-04). What starts a figure anew:
a build that finished (`finished: true`), and an uninstall, which deletes the
folder. A fresh install never meets an earlier figure: with no install record
the guard lets through only an empty folder -- this file is deliberately NOT
one of `OUR_OWN_FILES`, so a folder holding it is not empty -- or a git
checkout, which the clone stage then refuses.
"""


BUILD_CACHE_ASKING = (
    "Asking Docker how much build cache it already holds, so that if this build fails, the "
    "next press is not asked again for the space it took. This can take up to two minutes."
)
"""Said before `stage_build` asks (T203): `buildx du`, then `system df`, get 60 s each."""


def write_build_cache_baseline(
    server_dir: Path, cache_bytes: int | None, *, finished: bool = False
) -> None:
    """Record the build cache this build starts on; `None` (Docker would not say) credits nothing.

    `finished=True` once the build has ended well, so the next build starts its
    own figure. Best-effort: a file that cannot be written is logged, and a
    missing or torn one credits nothing.
    """
    try:
        (server_dir / BUILD_CACHE_FILE).write_text(
            json.dumps({"baseline_bytes": cache_bytes, "finished": finished}) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning(f"could not record the build cache this build starts on: {exc}")


def _build_cache_record(server_dir: Path) -> dict[str, object] | None:
    try:
        parsed = json.loads((server_dir / BUILD_CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def read_build_cache_baseline(server_dir: Path) -> int | None:
    """The recorded starting figure, or `None` for a missing, unreadable or garbled file."""
    record = _build_cache_record(server_dir)
    value = record.get("baseline_bytes") if record is not None else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def build_cache_baseline_for(server_dir: Path, measured: int | None) -> int | None:
    """The figure a build starting now on `measured` bytes of cache records (`BUILD_CACHE_FILE`).

    After a build that did not finish, the lower of its figure and `measured`:
    the cache has only grown by what that build compiled, which this one reuses,
    or it was pruned, and then only what is there now can be reused. `None` on
    either side is "not known", so the other figure stands.
    """
    record = _build_cache_record(server_dir)
    earlier = (
        read_build_cache_baseline(server_dir)
        if record is not None and record.get("finished") is not True
        else None
    )
    if earlier is None:
        return measured
    if measured is None:
        return earlier
    return min(earlier, measured)


def folder_bytes(folder: Path) -> int | None:
    """What the files under `folder` add up to, in bytes; `None` when it cannot be listed (T203).

    Preflight credits a resumed install with what its finished stages already
    put in the server folder (`Spent.server_dir_bytes`). Found by the m910q
    live test of PR 294 (2026-10-04): a one-drive press after a lost builder
    was asked again for the whole server-folder share although the 2.20 GiB
    checkout it sizes was already there.

    Every miss counts SHORT, which credits less and so asks for more: a link or
    a Windows reparse point (junction, OneDrive placeholder) is neither entered
    nor counted, since what it stands for is not this folder's, and an entry or
    a subfolder that cannot be looked at adds nothing. Only a folder that cannot
    be listed at all is `None`, which credits nothing.
    """
    total = 0
    pending = [folder]
    first = True
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            if first:
                logger.info(f"could not measure {folder}: {exc}")
                return None
            continue
        first = False
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            # A symlink looked at without following it is neither a folder nor a
            # file below, so it is skipped there; a Windows junction looks like a
            # folder and is told apart only by its reparse-point attribute.
            if getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                continue
            if stat.S_ISDIR(st.st_mode):
                pending.append(Path(entry.path))
            elif stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


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
    corrections: CorrectionCheck | None = None
    """The press was a CORRECTIONS press (T129): the check the person agreed to, else None.

    `updates_only`'s promise, for the same reason and read at the same point:
    the family branches on it before `stage_import()` is called, so neither the
    full import nor the `partial` arm's `DROP DATABASE` is reachable on this
    route. The press also keeps `updates_only` set, so a family that never
    learned to read this field still takes the older route, which refuses
    anything that does not read as a finished import (cold review, round 1).
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


@dataclass(frozen=True)
class ServersDownWork:
    """What a family does in a rebuild's window with the servers down (T179 Task 6, fix round 1).

    The TrinityCore update route imports the world tables an update changed with
    the NEW code's servers stopped and before they start, so a build that needs
    the new tables meets them on its first start rather than failing its ready
    wait on the old ones; and on a rollback it imports the same files from the
    old checkout, so the old build meets its own tables again. Handed to
    `rebuild()` by `update_to_latest()` (from `servers_down_work()`); a plain
    Rebuild has none, and its recreate is the one compose call it always was.
    """

    prepare: Callable[[], Iterator[str]]
    """Before the servers are stopped; a raise here leaves nothing touched."""
    forward: Callable[[StageContext], Iterator[str]]
    """Servers down, the new build tagged, before it starts; a raise rolls the rebuild back."""
    back: Callable[[StageContext], Iterator[str]]
    """The rollback's window: servers down, the old build tagged back, before it starts.

    A raise is said in the rollback's sentence; the rollback goes on to start the
    old build regardless.
    """
    settle: Callable[[], None] = lambda: None
    """After a failed press, on every way out (fix rounds 2-3): undo what `prepare()` wrote
    when neither `forward()` nor `back()` began.

    A stop given up during the load wait, or one that failed, imports nothing; a
    record saying something is waiting would then be false. Must not raise.
    """
    keep: Callable[[], Iterator[str]] = lambda: iter(())
    """Instead of `settle()`, when the rollback stopped before the old build was back (T197).

    The new build stays on its tags, so what it needs and `forward()` did not get to
    is left waiting for it rather than undone: there, a record naming the tables to
    import is true. A raise is said in the press's sentence, not in its place.
    """
    done: Callable[[], Iterator[str]] = lambda: iter(())
    """Once the new build is up, or kept after its banner (T197 fix round 6).

    Nothing the work recorded is removed before this: a build that fails its ready
    wait is rolled back, and the old build still needs what the record owed then.
    """
    database: Callable[[], str | None] = lambda: None
    """What the rollback says about the database, read after `back()` (T217); None = its own.

    The rollback's own sentence says the database is NOT put back. The update route
    puts back its copy of the databases a new build changes (`snapshot`), and then
    this says what went back, or that nothing was changed because the new build
    never started.
    """
    came_back: Callable[[], None] = lambda: None
    """Once the old build the rollback started has REPORTED READY (T217 live proof, item 5).

    The update route forgets its older copies here and nowhere earlier in a
    rollback: until the old build is up, an older copy may be the only one that
    can bring it back. Must not raise.
    """
    did_not_come_back: Callable[[], Callable[[str | None], str] | None] = lambda: None
    """When the old build the rollback started did not report ready (T217 live proof, item 5).

    A sentence-maker means: stop its servers rather than leave them restarting,
    then say what it returns -- given None once they stopped, or why the stop
    failed (scoped re-review: "stopped" only after a stop) -- in place of
    `database()`'s. None keeps the rollback's own sentence, with the servers as
    they are. Must not raise.
    """
    finishes_start_refusal: bool = True
    """This work finishes what refuses a start, so `rebuild()` need not ask first (T217).

    True for T179's world tables, which `forward()` imports. The update route now
    hands every family a `ServersDownWork` (its sources and its database copy go
    back inside `back()`), and for a family with no work of its own this is False,
    so `rebuild()` still asks `start_refusal()` before anything is compiled, as it
    always did with no work at all.
    """


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


# T64's four git questions, bound the way `_git_file_unmodified` is: a module
# function per question, named as a `Seams` default, and NOT a Protocol. The
# five `runtime_checkable` Protocols in `git.py` exist so that `apply.py` can
# narrow a real `Git` it was handed and fall back when it was handed a fake;
# this engine is never handed one -- every external effect it has is already a
# `Seams` field, and a test fakes the field. Adding Protocols here would be a
# second way to answer the same question, which is the shape §27 of the bug
# checklist is about.


def _git_local_edits(dest: Path, ignoring: Sequence[str] = ()) -> tuple[str, ...] | None:
    """`git status --porcelain` inside a container, minus the app's own patch paths."""
    return git.ContainerGit().local_edits(dest, ignoring)


def _git_no_local_commits(dest: Path, branch: str | None) -> bool | None:
    """Does this checkout carry commits the update would drop? Containerised."""
    return git.ContainerGit().no_local_commits(dest, branch)


def _git_head_sha(dest: Path) -> str | None:
    """The full commit this checkout is on, containerised."""
    return git.ContainerGit().head_sha(dest)


def _git_head_version(dest: Path) -> str | None:
    """`a1b2c3d · 2026-09-16` for this checkout, containerised."""
    return git.ContainerGit().head_version(dest)


def _git_commits_since(dest: Path, rev: str) -> int | None:
    """How many commits this checkout carries past `rev`, containerised."""
    return git.ContainerGit().commits_since(dest, rev)


def _installed_release(state: InstallState | None, repo: str) -> str:
    """The release tag the install record says `repo` was moved to, or `""` (T126)."""
    rev = state.rev_for(repo) if state is not None else None
    return rev.release if rev is not None else ""


def _upstream_get(url: str, accept: str) -> bytes:
    """`upstream.https_get`, looked up at CALL time.

    Not `= upstream.https_get` bound into `Seams` at import, for the reason
    `Seams.selinux_enforcing` records: a default bound at class definition is a
    function object no later patch of the module can reach, so the suite's
    guard against real GitHub calls (`conftest`) could not see it (T124 review).
    """
    return upstream.https_get(url, accept)


def _git_restore_rev(dest: Path, rev: str) -> None:
    """Put this checkout back on `rev`, containerised. Raises `git.GitError`."""
    git.ContainerGit().restore_rev(dest, rev)


def _git_changed_files(
    dest: Path, old: str, new: str, paths: Sequence[str]
) -> tuple[tuple[str, str], ...] | None:
    """Which files under `paths` differ between two commits of this checkout, containerised."""
    return git.ContainerGit().changed_files(dest, old, new, paths)


def _git_changed_lines(dest: Path, old: str, new: str, path: str) -> tuple[str, ...] | None:
    """One file's `+`/`-` lines between two commits of this checkout, containerised."""
    return git.ContainerGit().changed_lines(dest, old, new, path)


READY_CEILING_SECONDS = 6 * 60 * 60
"""The outer bound on the ready wait, whatever the server is saying.

NOT a load budget, and the refusal that names it says so. `wait_for_ready()`
grants a server that is still printing another window every time it prints, so
without an outer bound an install could wait for ever — this is where that stops.
(Until T247 not even Stop ended it: `wait_ready()` took no cancel. It does now,
through `docker.ReadySpec.cancel`, and that is the player's way out; this bound
is the one for a wait nobody is watching.)

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

READY_WAIT_STOPPED = (
    "Stop was pressed while the world server was loading, so the wait for it ended before it "
    "reported ready: this build was never seen to come up."
)
"""What a ready wait ended by the player's Stop says (T247), first in whatever follows.

A rebuild rolls back on it, as on any ready wait that failed, and appends what
the rollback did; an install adds `INSTALL_LEFT_LOADING`.
"""

INSTALL_LEFT_LOADING = (
    "The server's containers were started and are left running, and its world server is left "
    "to finish starting. Nothing was stopped: a world is never stopped in the middle of its "
    "load, which is when it updates its database. Press Install again on the same folder to "
    "wait for it and finish the install; the steps already done are not done again."
)
"""What an install stopped in its ready wait has left (T247), said before the Stop ends it.

True by construction: the stages before `ready` are done (`up` started the
containers), and `ready` and `up` are never recorded, so the next Install on
the folder skips every recorded stage, runs `start-db`/`up` again on a server
already up, and then waits again. Not stopping is T158's rule, the owner's:
never kill a world mid-load (`docker.wait_for_the_world_to_load()`). Driven in
`test_an_install_stopped_in_its_ready_wait_resumes_at_the_wait`.
"""

INSTALL_LEFT_RUNNING = (
    "The server's containers are left running, and its world server had reported ready. "
    "Nothing was stopped. Press Install again on the same folder to finish the install; the "
    "steps already done are not done again."
)
"""`INSTALL_LEFT_LOADING` for a Stop inside the watch after the banner (Codex review, T247):
the world HAD reported ready, so it is not "left to finish starting"."""

READY_WAIT_STOP_HINT = (
    '"Stop now anyway" ends this wait now and puts the build from before back at once: the new '
    "world may then be force-stopped in the middle of its load, and its database left "
    "half-updated."
)
"""Said under `docker.STOP_WAITS_FOR_THE_LOAD` when the panel has the escape (T247 review)."""

READY_WAIT_STOPPED_ANYWAY = (
    '"Stop now anyway" was pressed while the world server was still loading, so the wait for it '
    "ended before it reported ready: this build was never seen to come up."
)
"""A rebuild's ready wait ended by the escape, not by the load (T247 review)."""

READY_STOPPED_AFTER_LOADING = (
    "Stop was pressed while the world server was loading, and it was let finish: it reported "
    "ready and stayed up for the minute it is watched, so this build did come up. The press "
    "asked for it to be stopped, so it is put back."
)
"""A rebuild whose Stop waited out the load, which then succeeded (T247 review, the lead's ruling:
the player asked to stop, so it rolls back)."""

READY_STOPPED_IN_THE_WATCH = (
    "The world server reported ready, and Stop was pressed during the minute it is watched "
    "afterwards, before that minute was over, so this build was not proved to stay up."
)
"""Stop inside T71's watch: true about the banner, and about the proof that was not finished."""

READY_ANYWAY_IN_THE_WATCH = (
    "Stop was pressed while the world server was loading, and it was let finish; it reported "
    'ready, and "Stop now anyway" was then pressed during the minute it is watched, so this '
    "build was not proved to stay up."
)
"""`READY_STOPPED_IN_THE_WATCH` for the escape pressed in the watch after a Stop during the load
(T247 review): the first press came in the load, not in the watch, and the sentence says so."""

READY_STOP_TOO_LATE = (
    "Stop was pressed after the new build had already been watched for the whole minute, so it "
    "came too late to matter: the build is kept."
)
"""Stop in the watch's last pause: the build met T71's proof first, so it is a Stop after
success (T247 review). Said before "The server is up."."""

READY_CRASHED_AFTER_STOP = (
    "Stop was pressed, and the new build then crashed: its world server reported ready and "
    "stopped again inside the minute it is watched, so the build from before goes back."
)
"""A rebuild whose Stop waited out the load, after which the world crashed in the watch (the
lead's ruling, 2026-10-05). Without a Stop that crash keeps the build (T71's
`WorldStoppedAfterReadyError`); with one, the press and the crash both point back."""

ROLLBACK_WAIT_UNSTOPPABLE = (
    "Waiting for the build from before to come up, now that it is back. Stop cannot end this "
    "wait: the rollback has already replaced the new build, and only this wait can say whether "
    "the build it put back is up."
)
"""Said once, before the rollback's own ready wait, which is handed no cancel (T247 review)."""

MANAGEMENT_WAIT_STOPPED = (
    "Stop was pressed, so Yu'lon stopped waiting for the world server. The server was not "
    "stopped: it is still loading, and the Server tab shows when it is up."
)
"""What `wait_ready_quietly()` logs when its cancel ends the wait (T247 review, T158)."""

READY_GRACE_SECONDS = 60.0
"""How long the world server is WATCHED after it prints its ready banner (T71).

A banner is a promise about a moment, not about a server, and until 2026-09-16
this app believed it for ever: `wait_ready()` returned True on the first match
and nothing looked again. Measured live on 2026-09-16 (gate `t63-owed-live`,
WoW WotLK + mod-city-bots): the world initialised in 1 m 21 s, printed
`... ready...`, and one line later aborted on `[1146] Table
'acore_world.city_bot_poi' doesn't exist` — the module's db-world SQL had not
been applied — then crash-looped three times, while the panel read "The server
is up" and "WoW WotLK was rebuilt and is running".

Sixty seconds because of what that same log says happens after the banner. The
line straight after `ready...` is `[mod-city-bots] loaded 219 city POIs`: the
first database work of the running world starts within a second of the banner,
and in the failing run that query is the one that aborted. The work that
follows it — 500 bots logging in, ten to a line — is the rest of the post-banner
startup, and it is tens of seconds long. A minute covers both with margin.

It is an upper bound, not a wait: `watch_after_ready()` returns the moment the
world is seen to have gone, so only a server that stays up spends the whole
minute, and what it costs that server is one extra minute of being watched
before the app says it is up — said out loud, in `wait_for_ready()`'s own
announcement, rather than as a pause.

Deliberately NOT long enough to cover the whole second boot of a crash loop
(1 m 21 s of world init, in that measurement): the first restart is seen within
one poll, and waiting for the second one would double what a healthy start
pays to learn nothing new.
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
                      `.notes/gates/7.7-win11-tortoise/README.md`,
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
`.notes/gates/gate-79-controller-surface.py`, three times per run (lines 219,
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


def _spell_elapsed(seconds: float) -> str:
    """`31 -> "31 seconds"`, `70 -> "1 minute 10 seconds"`, `180 -> "3 minutes"` (T223).

    For the waits for Docker, which measure seconds and say them: `_spell_seconds()`
    rounds to whole minutes from 30 seconds up, and a 31-second wait read "Docker
    answered after 1 minute." on yulon-win11 (2026-10-05).
    """
    total = int(round(seconds))
    if total == 0:
        return "less than a second"
    minutes, rest = divmod(total, 60)
    parts = [f"{minutes} minute{'s' if minutes != 1 else ''}"] if minutes else []
    if rest:
        parts.append(f"{rest} second{'s' if rest != 1 else ''}")
    return " ".join(parts)


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


_STILL_UP_STATUSES = ("", "running")
"""Container statuses that are not "it has stopped since it said it was ready".

Two, where `_ALIVE_STATUSES` has three, and `restarting` is the difference. It
belongs there and not here because the same word means two things either side of
the banner: BEFORE it, a container in restart backoff is on its way up and the
wait must not call it dead; AFTER it, the run that printed the banner has ended,
which is the whole of what T71 is about.

`""` — a `docker inspect` that would not answer (`docker.ContainerState()`) —
stays on this list on purpose, and it is the one entry that is not a fact about
the server. Before this watch existed an unreadable daemon left the app saying
"up", so reading it as a stop would invent a failure out of a hiccup where there
was not even a wrong sentence before. The conservative direction here is the
opposite of the one `_ALIVE_STATUSES` takes, because this watch runs only after
a server has already been seen to be up.
"""


@dataclass(frozen=True)
class AfterReady:
    """What `watch_after_ready()` saw: did the world stay up, and its last words if not.

    A pair rather than `str | None`, because the words can legitimately be `""`
    — a container that stopped having printed nothing new this run — and a
    caller testing the string for truth would then report that server as up.
    """

    stopped: bool
    words: str
    cut_short: bool = False
    """The watch was ended by its cancel before its minute was over (T247): nothing proved."""


_DYING_WORDS_LINES = 5
"""How many of a stopped world server's last lines are quoted back. `_restore_rollback()`'s number.

Five and not one, for what the T63 log looks like. The line that says WHY is not
the last line: after `[1146] Table 'acore_world.city_bot_poi' doesn't exist`
came the advice to run the sql/updates folders, `>> ABORTED`, and a source
location — so a refusal quoting the final line alone would hand the user
`# Location '/azerothcore/src/.../MySQLConnection.cpp:634'` and nothing about the
missing table. Which of a core's dying lines carries the diagnosis is a per-fork
fact this app has no business guessing (memory
`upstream-conventions-differ-per-fork`); a short block needs no guess.
"""


def _dying_words(texts: Sequence[str], fatal: str | None) -> str:
    """What to quote a stopped world server on, from the logs still in hand.

    `texts` is ordered by the caller — the reading likeliest to hold the death
    first — and asked in that order, twice over. A `fatal` match wins the first
    pass, because a catalogue `fatal` is this fork's own name for "the server
    said why" (Tortoise's already carries `\\[1146\\] Table .* doesn't exist`,
    the very line T63 measured) and one line is then the whole answer; it is
    widened to that line by `_line_around()` for the reason that function
    exists. The second pass is for the three entries that declare no `fatal` at
    all — wow-wotlk and both CMaNGOS games — and quotes the last
    `_DYING_WORDS_LINES` non-empty lines instead.

    Why more than one text: after a restart, `docker._logs(this_run_only=True)`
    is the log of the NEW run, and the abort that ended the old one is not in
    it. The caller keeps the reading from before the stop and hands both over.

    `""` when neither text holds anything — a container that stopped without
    printing a word this run. The callers say that in words rather than quoting
    an empty block.
    """
    for text in texts:
        found = re.search(fatal, text) if fatal is not None else None
        if found is not None:
            return _line_around(text, found)
    for text in texts:
        said = [line.strip() for line in text.splitlines() if line.strip()]
        if said:
            return "\n".join(said[-_DYING_WORDS_LINES:])
    return ""


def _still_the_run_that_said_ready(
    before: WorldOutput | None,
    now: WorldOutput,
    baseline: int | None,
    banner: str,
    fatal: str | None,
) -> AfterReady | None:
    """One reading, judged. `None` to keep watching; an `AfterReady` ends the watch.

    Four questions, and the third is the one the first version of this did not
    ask. In order:

    * **gone**: a status off `_STILL_UP_STATUSES`. The container's CURRENT log is
      then the log of the run that died, so it is quoted first.
    * **restarted**: `restarts` changed at all, against the first readable count
      this watch took. `!= 0` rather than `> 0`: a count that went DOWN is a
      container that was replaced rather than restarted, and the run that
      printed the banner is just as gone either way.
    * **the banner is not in this run's log any more**. `docker.wait_ready()`
      matched `banner` in the CURRENT run's log a moment ago — that is what made
      it answer True — so a readable log without it is a different run, whatever
      the count says. This is the only question that answers the FIRST reading:
      `restart: unless-stopped` backs off for 100 ms, and between the banner and
      this watch's first look sit `_auth_ready()`'s two docker commands and the
      caller's own, so a container that aborted a second after `ready...` can be
      up again under a new run before anything here has looked once. The count
      is then already the new one and there is nothing for "grew" to grow from.
      (Cold review, 2026-09-16, with the timings.)
    * **fatal**, for the world that aborts without the container noticing yet.

    An EMPTY log is not an answer to the third question: `docker._logs()` returns
    `""` both for "this run has printed nothing" and for "the read failed"
    (`_world_output()`'s docstring has that gap), and calling a failed read a
    restarted container would refuse a healthy server on a hiccup. The same rule
    `_read_world()` follows — the unknown value is never a verdict.

    `before` is `None` on the first reading and is what makes the words honest:
    after a restart the current log is the NEW run's, so quoting it as the dying
    words would attribute the fresh boot's lines to the crash. With no earlier
    reading in hand there is nothing to quote, and the caller says so instead.
    """
    earlier = (before.text,) if before is not None else ()
    if now.status not in _STILL_UP_STATUSES:
        return AfterReady(True, _dying_words((now.text, *earlier), fatal))
    grew = None if baseline is None or now.restarts is None else now.restarts - baseline
    if grew is not None and grew != 0:
        return AfterReady(True, _dying_words((*earlier, now.text), fatal))
    if now.text and now.status and not re.search(banner, now.text):
        return AfterReady(True, _dying_words(earlier, fatal))
    found = re.search(fatal, now.text) if fatal is not None else None
    if found is not None:
        return AfterReady(True, _line_around(now.text, found))
    return None


_MISSING_TABLE = re.compile(r"[Tt]able\s+\S*\s*(?:doesn't|does not) exist")
"""A world server saying a database table it needs is not there.

The T63 shape, and the only one of these the app can name a remedy for:
`[1146] Table 'acore_world.city_bot_poi' doesn't exist` — a module's db-world
SQL that was never applied, which the install report had already listed as left
unapplied. Deliberately narrow (a TABLE, and the two spellings of the verb), and
deliberately not a per-fork string: MySQL's own wording is what every core
passes through, while `[1146]` is AzerothCore's prefix and Tortoise's `fatal`
already spells it its own way.
"""

MODULE_SQL_HINT = (
    " That table is created by a module's db-world SQL, which has not been applied to this "
    "database. Press Apply module SQL on the Modules tab, then Start."
)
"""The one remedy this app can hand a user for a missing-table abort (owner, 2026-09-16).

It is the remedy the T63 gate ran by hand and recorded as working: Stop, Apply
module SQL (12 s), Start, up in 39 s. Named buttons, because "apply the module's
SQL" is a sentence and `Apply module SQL` is a thing to press.
"""


def _missing_table_hint(words: str, entry: CatalogEntry) -> str:
    """The remedy for a server whose dying words name a table that is not there.

    `_corrections_hint()` when a step `entry` offers to installs already
    imported creates exactly that table (T159) -- the T63 remedy would send the
    person to a Modules tab that has nothing to do with it -- else
    `MODULE_SQL_HINT`.
    """
    if not _MISSING_TABLE.search(words):
        return ""
    return _corrections_hint(entry, words) or MODULE_SQL_HINT


_MISSING_TABLE_NAMED = re.compile(r"[Tt]able\s+'([^'\s]+)'\s+(?:doesn't|does not) exist")
"""`_MISSING_TABLE` with the name captured, as MySQL quotes it: `'<schema>.<table>'`."""


def _corrections_hint(entry: CatalogEntry, words: str) -> str:
    """Name T129's button when a step it would offer creates the table the server died on (T159).

    Read off the catalog, never off a table name in code: the step is one of
    `correction_phases(entry)` whose statement is a `CREATE TABLE` of exactly
    that table. The table a statement merely mentions does not count --
    Tortoise's `character_inventory_copy` step names `character_inventory` as
    the table it copies, and a server missing THAT has a different problem.
    Empty when nothing matches, so every other game and every other table keeps
    the sentence it had.

    Not proof that THIS install is offered the step -- a server imported with
    it cannot be missing the table it makes -- and the button is on the Server
    tab only while it is; the sentence names both, so it cannot send anyone to
    a button that is not there without saying where to look.
    """
    for found in _MISSING_TABLE_NAMED.finditer(words):
        table = found.group(1).rsplit(".", 1)[-1]
        creates = re.compile(
            rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?{re.escape(table)}`?(?![\w$])",
            flags=re.IGNORECASE,
        )
        for phase in correction_phases(entry):
            if any(creates.search(statement) for statement in phase.statements):
                return (
                    f" That table is made by the install-plan step {phase.name!r}, which this "
                    f"server's databases were imported without. Press "
                    f'"{CORRECTIONS_BUTTON_LABEL}" in the banner on the Server tab -- it stops '
                    f"the world server, adds the table and leaves the world stopped -- then press "
                    f"Start."
                )
    return ""


def watch_after_ready(
    look: Callable[[], WorldOutput],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    *,
    interval: float,
    banner: str,
    fatal: str | None,
    grace: float = READY_GRACE_SECONDS,
    cancel: threading.Event | None = None,
) -> AfterReady:
    """Keep watching a world server for `grace` seconds AFTER it said it was ready (T71).

    ONE function for `_read_world()`'s reason: the install spine's
    `wait_for_ready()` and `wait_ready_quietly()` both call it, and two copies of
    a rule is two rules the day one of them is edited. What each reading means is
    `_still_the_run_that_said_ready()`, which holds the order and the argument;
    this loop is the clock around it.

    EVERY reading is judged, the first one included, and it is the first that
    costs the most to skip: the banner is up to a poll old by the time the wait
    returns, and a container that aborts a second later can be restarted and
    running again before this looks once. A first reading taken only as a
    baseline is a crash the watch then sits through for a minute and calls
    healthy.

    Returns as soon as it has an answer. A server that stays up costs the full
    `grace`; one that dies costs one poll after it died.

    `cancel` set (the job's Stop, T247) ends the watch after the look it lands
    in, answering `cut_short` -- nothing proved. After the look, not before it,
    so a world seen to have stopped is reported as that, Stop or no Stop; and a
    Stop that lands in the last pause, after which the minute is over, does not
    cut anything short: the build met its proof first. A Stop that
    lands in the pause is heard when the pause ends: at most `interval`, two
    seconds as shipped. Left so rather than waiting on the event, because
    `sleep` is the seam every test hands a clock-advancing fake, and a pause
    built from the event would be a real one in every test that hands the
    press a cancel (Codex adversarial review, 2026-10-05).
    """
    deadline = monotonic() + grace
    baseline: int | None = None
    before: WorldOutput | None = None
    # BOTH bounds, and the poll count is the one that guarantees an end. The
    # clock bound is the honest one — a `look()` is two docker commands and can
    # take a second of the minute on its own — but it is read off a seam, and a
    # caller whose clock does not move while this polls (every test that hands
    # over a fake one) would otherwise never leave. That is not a hypothetical:
    # it hung two of this file's own management tests before the count was added.
    # `+ 1` because the first look happens before any sleep.
    looks = max(1, int(grace / interval)) + 1
    for number in range(looks):
        now = look()
        stopped = _still_the_run_that_said_ready(before, now, baseline, banner, fatal)
        if stopped is not None:
            return stopped
        baseline = _restart_baseline(baseline, now)
        before = now
        if monotonic() >= deadline or number == looks - 1:
            # The whole minute was watched: proved, even if a Stop came in the
            # last pause (T247 review). No pause after the last look.
            break
        if cancel is not None and cancel.is_set():
            return AfterReady(False, "", cut_short=True)
        sleep(interval)
    return AfterReady(False, "")


def wait_ready_quietly(
    spec: docker.ContainerSpec,
    ready: docker.ReadySpec,
    *,
    wait: Callable[..., bool] | None = None,
    output: Callable[..., WorldOutput] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    wsl_distro: str | None = None,
    cancel: threading.Event | None = None,
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

    Returns True once the world server has reported ready AND stayed up for
    `READY_GRACE_SECONDS` afterwards — `watch_after_ready()` holds why a banner
    on its own is not an answer. Returns False for that watch, for
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

    `cancel` (T247 review) ends the WAIT and nothing else: False, with
    `MANAGEMENT_WAIT_STOPPED` logged. The world is left loading -- T158, never
    stopped mid-load -- because a Start or Restart that waits has nothing to put
    back. No press of the app's reaches this with a Stop today: Start and
    Restart on the Server tab return once the containers are started and wait
    for nothing (`controller_view.start_server()`, `_do_restart()`).
    """
    return watch_the_start(
        spec,
        ready,
        wait=wait,
        output=output,
        monotonic=monotonic,
        sleep=sleep,
        wsl_distro=wsl_distro,
        cancel=cancel,
    ).ready


StartVerdict = Literal[
    "ready", "stopped", "loop", "gone", "fatal", "quiet", "unreadable", "ceiling", "cancelled"
]


@dataclass(frozen=True)
class StartAnswer:
    """What became of a world server after a start, by the rule an install's ready stage uses.

    `verdict` is `ready` only once the world reported ready AND stayed up for
    `READY_GRACE_SECONDS` (`watch_after_ready()`). `stopped` is a world that said
    ready and was gone inside that watch; the others are `_read_world()`'s, plus
    `ceiling` for a server still printing when `management_ceiling()` ran out
    and `cancelled` for a wait its `cancel` ended (T247: the world left loading).

    `words` is what the server itself last said (`_dying_words()`, colour codes
    stripped), for Details; `""` when it said nothing or the answer is `ready` (T382).
    """

    verdict: StartVerdict
    words: str = ""

    @property
    def ready(self) -> bool:
        return self.verdict == "ready"


def watch_the_start(
    spec: docker.ContainerSpec,
    ready: docker.ReadySpec,
    *,
    wait: Callable[..., bool] | None = None,
    output: Callable[..., WorldOutput] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    wsl_distro: str | None = None,
    cancel: threading.Event | None = None,
) -> StartAnswer:
    """`wait_ready_quietly()` with the verdict and the server's words kept (T382).

    The bool answer is this one's `.ready`, so the two cannot drift into two
    rules. Kept apart from the bool because the Tuning tab's restart says WHICH
    way the world did not come up, with its own lines under Details: it said
    `restart: done.` over a crash loop on Tortoise (T302 live check, 2026-10-05).
    """
    wait = wait or docker.wait_ready_for
    look = output or _world_output
    clock = monotonic or time.monotonic
    pause = sleep or time.sleep
    ceiling = management_ceiling(ready.timeout)
    ready = replace(ready, cancel=cancel)

    def heard() -> bool:
        if cancel is not None and cancel.is_set():
            logger.info(MANAGEMENT_WAIT_STOPPED)
            return True
        return False

    started = clock()
    before = look(spec, wsl_distro=wsl_distro)
    first_restarts = before.restarts
    while True:
        window = min(ready.timeout, ceiling - (clock() - started))
        if window <= 0:
            return StartAnswer("ceiling", ansi.strip(_dying_words([before.text], ready.fatal)))
        if wait(spec, replace(ready, timeout=window), wsl_distro=wsl_distro):
            # The banner is not the verdict (T71): a world that says `ready...`
            # and then aborts on a missing table is not a server this may
            # answer True about. The words go back to the caller and to the log,
            # so a management wait that comes back False is not silent about why.
            after = watch_after_ready(
                lambda: look(spec, wsl_distro=wsl_distro),
                clock,
                pause,
                interval=ready.interval,
                banner=ready.world,
                fatal=ready.fatal,
                cancel=cancel,
            )
            if after.cut_short:
                # Only a set cancel cuts the watch short (T247).
                heard()
                return StartAnswer("cancelled")
            if not after.stopped:
                return StartAnswer("ready")
            logger.warning(
                f"{spec.world} reported ready and then stopped within "
                f"{_spell_seconds(READY_GRACE_SECONDS)}: {after.words!r}"
            )
            return StartAnswer("stopped", ansi.strip(after.words))
        if heard():
            return StartAnswer("cancelled")
        now = look(spec, wsl_distro=wsl_distro)
        first_restarts = _restart_baseline(first_restarts, now)
        verdict, _ = _read_world(before, now, first_restarts, ready.restart_loop, ready.fatal)
        if verdict != "alive":
            # The current run's log first, then the one before it: after a
            # restart the abort that ended the old run is not in the new one.
            return StartAnswer(
                cast(StartVerdict, verdict),
                ansi.strip(_dying_words([now.text, before.text], ready.fatal)),
            )
        before = now


def ready_after_start(
    entry: CatalogEntry,
    spec: docker.ContainerSpec,
    *,
    wsl_distro: str | None = None,
    wait: Callable[..., bool] | None = None,
    output: Callable[..., WorldOutput] | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> StartAnswer:
    """Wait for `spec`'s world after a start with `entry`'s own ready markers (T382).

    The markers an install's ready stage waits on (`ready_spec_for()`), so a
    restart from the Tuning tab is held to the rule the install was. An entry
    with no native block has no markers, and that is said rather than guessed.
    """
    native_block = entry.install.native
    if native_block is None:
        raise InstallerError(
            f"{entry.name} has no ready markers, so whether its world came up cannot be asked."
        )
    return watch_the_start(
        spec,
        ready_spec_for(entry, native_block.ready),
        wait=wait,
        output=output,
        monotonic=monotonic,
        sleep=sleep,
        wsl_distro=wsl_distro,
        cancel=cancel,
    )


def ready_spec_for(entry: CatalogEntry, markers: ReadyMarkers) -> docker.ReadySpec:
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
    tokens = {"REALM_HOST": INSTALL_REALM_HOST, "WORLD_PORT": str(entry.ports.world)}

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
                f"{entry.name}'s ready marker {text!r} is not a usable pattern "
                f"({exc}). Fix its ready marker in catalog.json; nothing was started."
            ) from exc
        return pattern

    try:
        world = marker(markers.world)
        auth = marker(markers.auth) if markers.auth is not None else None
        fatal = marker(markers.fatal) if markers.fatal is not None else None
    except composegen.ComposeGenError as exc:
        raise InstallerError(f"{entry.name}'s ready markers are broken: {exc}") from exc
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


FAILURE_TAIL_LINES = 500
"""How many of a container's last lines a failed `ready` shows (T249), per stream.

Shown on the install's log, where the panel keeps 5000 lines, so the world and
login servers together take a fifth of it; written in full to the run log the
support file zips. A crash-looping world server's last start is a few hundred
lines, and its reason is at the end.
"""

FAILURE_TAIL_BYTES = 256 * 1024
"""A cap on those lines, END kept, for a server that prints very long lines."""

FAILURE_TAIL_TIMEOUT_S = 20.0
"""Per read: a failure is being reported, and a slow daemon must not hold it up long."""


def _container_tail(container: str, *, wsl_distro: str | None = None) -> str | None:
    """`docker.last_lines()` of one container, for a failed `ready` (T249). `None`: unread."""
    return docker.last_lines(
        container,
        FAILURE_TAIL_LINES,
        wsl_distro=wsl_distro,
        timeout=FAILURE_TAIL_TIMEOUT_S,
        max_bytes=FAILURE_TAIL_BYTES,
    )


_STOPPED = object()
"""`_unless_stopped()`'s answer when Stop was pressed before the read came back."""

_STOP_POLL_S = 0.1


def _unless_stopped(
    read: Callable[[str], str | None], container: str, cancel: threading.Event | None
) -> object:
    """`read(container)`, or `_STOPPED` as soon as `cancel` is set (T249 review).

    A tail read is bounded at `FAILURE_TAIL_TIMEOUT_S`, two of them 40 s, and a
    Stop pressed while a failure is being reported must not wait that out. The
    read runs on a daemon thread that is left to finish on its own: it holds no
    lock, writes nothing, and its own timeout ends it.
    """
    if cancel is None:
        return read(container)
    if cancel.is_set():
        return _STOPPED
    answer: list[str | None] = []
    worker = threading.Thread(
        target=lambda: answer.append(read(container)), name="yulon-tail-read", daemon=True
    )
    worker.start()
    while worker.is_alive():
        if cancel.wait(_STOP_POLL_S):
            return _STOPPED
    return answer[0] if answer else None


def _kept_end(text: str, limit: int) -> str:
    """`text`'s last `limit` bytes, starting on a whole line."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    tail = data[len(data) - limit :].decode("utf-8", errors="ignore")
    _, newline, whole = tail.partition("\n")
    return whole if newline else tail


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
    # T64's five. Import-bound like every other seam here except the four that
    # say why they are not: nothing else in this app asks these questions, so
    # there is no second answerer to split from, and the one caller
    # (`update_to_latest()`) is reached only from a button a test drives with
    # its own `Seams`.
    local_edits: Callable[[Path, Sequence[str]], tuple[str, ...] | None] = _git_local_edits
    no_local_commits: Callable[[Path, str | None], bool | None] = _git_no_local_commits
    head_sha: Callable[[Path], str | None] = _git_head_sha
    head_version: Callable[[Path], str | None] = _git_head_version
    commits_since: Callable[[Path, str], int | None] = _git_commits_since
    upstream_get: upstream.HttpGet = _upstream_get
    """The one network read T124's line makes: GitHub's compare API, per source.

    A seam for the reason every network call here is one: a test that could
    not count the GETs could not show the line is asked at most once a day, and
    one that could not make them fail could not show "no network" says nothing.
    """
    restore_rev: Callable[[Path, str], None] = _git_restore_rev
    """Put one source back on the commit it was on before this press moved it.

    A seam and not a `git` call, for `recreate`'s reason one size down: a test
    that could not see the restore happen -- and could not make it FAIL -- could
    not see either of the two defects that make the rollback decorative. A
    source left ahead of the image compiled from it is a folder and an image
    that disagree, which is the state `update_to_latest()`'s whole failure path
    exists to prevent, and it is invisible from the outside.
    """
    changed_files: Callable[[Path, str, str, Sequence[str]], tuple[tuple[str, str], ...] | None] = (
        _git_changed_files
    )
    """T179: `(status, path)` of each file under some paths that differs between two commits.

    What the TrinityCore family's update route asks right after a move -- about
    the commit a checkout moved from and the one it landed on -- before it lets
    the compile start. `None` is git that could not say, which the route refuses.
    """
    changed_lines: Callable[[Path, str, str, str], tuple[str, ...] | None] = _git_changed_lines
    """T179: one file's `+`/`-` lines between two commits; `None` when git could not say."""
    images_built: Callable[[Sequence[str]], bool | None] = docker.images_built
    build_cache_bytes: Callable[[], int | None] = docker.build_cache_bytes
    """How much build cache Docker holds; preflight counts it for a resumed build (T203)."""
    folder_bytes: Callable[[Path], int | None] = folder_bytes
    """What the server folder already holds; preflight counts it for a resumed build (T203)."""
    image_id: Callable[[str], str | None] = docker.image_id
    """T224/T225: the image a ref names, or None. A rebuild reads whether its live tags moved
    (against their `-rollback` names) and whether a kept build's names still hold it."""
    build: Callable[..., docker.AttachedRun] = docker.build_staged
    context_fingerprint: Callable[..., str | None] = build_context.fingerprint
    """T224: the fingerprint of what a build of the server folder is made from, or None.

    Read on the host, where the files are. A server inside a WSL distro answers
    None (`in_wsl()`): walking `\\\\wsl.localhost` would boot the distro (T133) and
    cross 9p for every file, so its finished builds are not kept (owner, D4)."""
    one_shot: Callable[..., docker.AttachedRun] = docker.run_one_shot
    end_one_shot: Callable[..., docker.OneShotLeft | None] = docker.end_one_shot
    """T539: end what of a one-shot service is still running; None once nothing is."""
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
    folder_projects: Callable[[Path], tuple[str, ...] | None] = docker.folder_projects
    """The compose project of each container brought up from a folder (T170).

    What Repair server files asks before it offers to write Yu'lon's compose
    file over the repository's own: containers of this folder under another
    project are where the characters are (`_upstream_compose_facts()`).
    """
    project_container_images: Callable[[str], tuple[tuple[str, str, str], ...] | None] = (
        docker.project_container_images
    )
    """(container, image ref it was made from, image id) for a project's containers (T170).

    What a rebuild keeps as its rollback when the image names are gone and the
    containers still hold the build by id (`_keep_rollback()`).
    """
    # `object` rather than `None`: `start_database()` has said since T7 whether
    # it HAD to start the container, for `apply.Applier`'s report line. This
    # stage ignores that -- it wants the database up, and it is up either way --
    # and the annotation says "whatever it answers" rather than pinning a
    # return this seam's own fakes do not have to produce.
    start_db: Callable[..., object] = docker.start_database
    """Called with `because=`, which ends `start_database()`'s timeout sentence (T248)."""
    start: Callable[[docker.ContainerSpec, Path], bool] = docker.start_staged
    recreate: Callable[..., bool] = docker.recreate_staged
    """`start` with `--force-recreate`, and the rebuild's only reason to exist as a seam.

    A separate field rather than a keyword on `start`, because the two are
    different requests and a test that could not tell them apart could not see
    the bug: a rebuild that recreated nothing would leave the pre-rebuild
    binary running behind an hour of perfectly correct compiler output.
    `docker.staged_up_argv()` holds why the force is asked for rather than left
    to compose.

    `Callable[...]` since T158: `stage_recreate()` passes `control=` (the load
    wait's `docker.StopControl`, carrying the rebuild's Cancel) and
    `before_signal=` (the moment past which something may have been touched).
    """
    mark_realm_offline: Callable[..., object] = realm_flag.mark_offline
    """Set the realm row's offline bit before the world starts or is replaced (T577).

    Called with `(entry, spec, server_dir)`; a no-op for an entry whose realm row names no
    flag column. Best effort: it logs what it could not do and never raises.
    """
    clear_realm_offline: Callable[..., object] = realm_flag.clear_offline_if_world_up
    """Take the bit off again when a replace gave up with the old world still running (T577)."""
    stop_servers: Callable[..., None] = docker.stop_servers_staged
    """The rollback's stop of the FAILED build's servers, before any tag moves back (T158).

    `Callable[...]` for `recreate`'s reason: it is called with the stop's
    `control=` (`docker.StopControl`), whose load wait may hold it.
    """
    tag_image: Callable[[str, str], str] = docker.tag_image
    remove_image: Callable[..., str] = docker.remove_image
    """The rebuild's rollback: kept as a second tag before the compile, let go as one after.

    `Callable[...]` because of the second argument, `force=`, which `_let_go()`
    passes only after the daemon has said in its own words that the removal
    `must be forced` -- see `docker.remove_image()` for why a name the rebuild
    made can end up held by a container the rebuild never touches. A double that
    accepts `ref` alone therefore still answers the first ask and fails loudly on
    the retry, which is the right way round: a fake that silently swallowed the
    force would be a fake that cannot see T79's defect.

    Two seams and not one `docker` handle, for the reason `recreate` is its
    own: a test that could not see the tag happen BEFORE the build, or the
    restore happen AFTER the ready wait failed, could not see the two bugs
    that would make the rollback decorative -- a rollback tagged after the
    compile is a copy of the new build, and one restored without a recreate
    leaves the failed build running.
    """
    wait_db_healthy: Callable[[docker.ContainerSpec], bool] = docker.wait_db_healthy_for
    wait_ready: Callable[[docker.ContainerSpec, docker.ReadySpec], bool] = docker.wait_ready_for
    container_tail: Callable[[str], str | None] = _container_tail
    """A container's last lines, read when `ready` fails (T249). `None`: docker would not say.

    A failed install is never remembered, so the support file has no install to
    read a container for; the lines it needs travel in the run's own log instead.
    """
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
    host_zone: Callable[[], str] | None = None
    """This computer's time zone, which a NEW install's override gets (T171). Read via `ask_zone()`.

    A late lookup for `selinux_enforcing`'s reason: the suite pins
    `time_zone.host_zone` (conftest) so no render depends on the zone of the
    box running it, and a default bound at import would slip past that pin.
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
    sleep: Callable[[float], None] = time.sleep
    """The only place this engine waits without a docker command waiting for it.

    `wait_ready()` does its own sleeping inside the window it is handed, so the
    spine never had to; `watch_after_ready()` (T71) polls on its own clock after
    the banner and does. A seam beside `monotonic` and for the same reason: a
    test that cannot advance the fake world's clock cannot drive the minute
    after a server said it was up, and a real `time.sleep` in that test would
    buy a minute of nothing per case.
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
    folder_claim: Callable[[Path, str, threading.Event | None], AbstractContextManager[object]] = (
        docker.folder_claim
    )
    """T543: a Re-extract's claim on its `data/`, made by the daemon that runs the tools.

    What it yields is a `docker.ClaimHeld` (T549: whether the claim was lost mid-press);
    a stand-in that yields anything else is a claim nobody watches."""
    copy_from_image: Callable[[str, str, Path], None] = docker.copy_from_image
    exec_stdin: Callable[..., subprocess.CompletedProcess[str]] = docker.exec_stdin
    sql_query: Callable[[str, str, str, str | None, str], str] = docker.sql_query
    volume_exists: Callable[[str], bool] = docker.volume_exists
    read_database: Callable[[CatalogEntry, Path], database_presence.Reading] = (
        database_presence.take_reading
    )
    """Is this install's database there at all (T377)? Asked by Rebuild and by Repair.

    `database_presence.take_reading()`: `missing` (no volume), `empty` (no login
    database), `present`, or `unknown` when Docker could not say. A Rebuild
    refuses the first two before it compiles; Repair runs only on them.
    """
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

    distro: str | None = None
    """The WSL distro these seams address (`in_wsl()`), or None for this host (T179).

    Not a seam: a name, so a sentence can say where a press is that this app can
    only offer inside that distro (the TrinityCore map-data extraction).
    """

    install_id: Callable[[Path], str] | None = None
    """The id this install's images and compose project are named after; None = hash the folder.

    `None` is every install this app makes on this machine: the id IS the hash
    of the folder, and `_install_id()` computes it. A server inside a WSL distro
    (T125) answers with the id its record carries instead (`recorded_install_id`),
    because Yu'lon on Linux hashed the distro's spelling of the folder and the
    Windows spelling hashes to a different one -- which stays the Windows-side
    key (credentials, dbsecret, run records) and must not name its images.
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
    stop_world: Callable[..., None] = docker.stop_containers
    """Stop the world server by name. Used by ONE press: T129's corrections, since T159.

    `Callable[...]` for `recreate`'s reason: it is called with `known=` (the
    install's spec, so T158's load wait applies), the stop's `control=` and a
    `deadline=` for the `docker stop` command itself.

    Its own field and not `stop_db` pointed at another container, for
    `db_running`'s reason: the two are asked for opposite reasons -- one puts
    back a database a press started, the other takes down a world a press is
    about to write under -- and a test that could not tell them apart could
    not see the order. `docker.stop_containers()` for `stop_db`'s reason too:
    one container, never the compose project, because `stop_staged()` takes the
    database down with it and the press needs the database.
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

    def ask_zone(self) -> str:
        """This computer's time zone, through the seam if one was given, else the host (T171)."""
        ask = self.host_zone
        return (ask if ask is not None else time_zone.host_zone)()

    @classmethod
    def in_wsl(cls, distro: str) -> Seams:
        """Seams for a server that lives inside the WSL distro `distro` (T125).

        The erasure recorded above -- four of the 7.3 primitives take a
        `wsl_distro` these types do not carry -- is closed HERE for the one kind
        of install that needs it, rather than by widening the types: every seam
        whose default can name a daemon is that same function with the distro
        bound, every git question runs on the distro's Docker through
        `git.ContainerGit(wsl_distro=)`, and `platform_id` is "linux" because the
        install was made by Yu'lon on Linux inside the distro -- which is what
        makes the recipe, the compose files and the image names it re-renders
        the ones it rendered then.

        What a rebuild or an update never asks is not addressed to the distro
        but REFUSED: provisioning, the install's preflight, the extraction and
        conf containers and the import check belong to an install, and Yu'lon
        does not install into a distro (`pyplan/wsl-resident-servers.md` §7).
        A refusal there is loud; the local default would be a quiet question to
        the wrong Docker. `tests/test_wsl_update_route.py` derives the list of
        seams that must be bound from their signatures, so a new docker seam
        added to this class fails that test until it is bound here.
        """
        repo = git.ContainerGit(wsl_distro=distro)
        on = functools.partial

        def refused(what: str) -> Callable[..., Any]:
            def refuse(*_args: object, **_kwargs: object) -> Any:
                raise InstallerError(
                    f"{what} belongs to an install, and this server lives inside the WSL "
                    f"distro {distro}; Yu'lon rebuilds and updates it there but does not "
                    "install into it. Nothing was started. That is a bug in this build."
                )

            return refuse

        return cls(
            platform_id=lambda: "linux",
            docker_ready=on(docker.daemon_ready, wsl_distro=distro),
            ensure_docker=refused("Setting Docker up"),
            dir_problem=refused("Checking a new server folder"),
            gather=refused("The install's preflight"),
            clone=repo.clone,
            remote_url=repo.remote_url,
            file_unmodified=repo.is_unmodified,
            local_edits=repo.local_edits,
            no_local_commits=repo.no_local_commits,
            head_sha=repo.head_sha,
            head_version=repo.head_version,
            commits_since=repo.commits_since,
            restore_rev=repo.restore_rev,
            changed_files=repo.changed_files,
            changed_lines=repo.changed_lines,
            images_built=on(docker.images_built, wsl_distro=distro),
            build_cache_bytes=on(docker.build_cache_bytes, wsl_distro=distro),
            image_id=on(docker.image_id, wsl_distro=distro),
            build=on(docker.build_staged, wsl_distro=distro),
            context_fingerprint=_no_fingerprint,
            one_shot=on(docker.run_one_shot, wsl_distro=distro),
            end_one_shot=on(docker.end_one_shot, wsl_distro=distro),
            verify_import=refused("Checking a database import"),
            container_exists=on(docker.container_exists, wsl_distro=distro),
            container_project=on(docker.container_project, wsl_distro=distro),
            container_working_dir=on(docker.container_working_dir, wsl_distro=distro),
            folder_projects=on(docker.folder_projects, wsl_distro=distro),
            project_container_images=on(docker.project_container_images, wsl_distro=distro),
            start_db=on(docker.start_database, wsl_distro=distro),
            start=on(docker.start_staged, wsl_distro=distro),
            recreate=on(docker.recreate_staged, wsl_distro=distro),
            mark_realm_offline=on(realm_flag.mark_offline, wsl_distro=distro),
            clear_realm_offline=on(realm_flag.clear_offline_if_world_up, wsl_distro=distro),
            stop_servers=on(docker.stop_servers_staged, wsl_distro=distro),
            tag_image=on(docker.tag_image, wsl_distro=distro),
            remove_image=on(docker.remove_image, wsl_distro=distro),
            wait_db_healthy=on(docker.wait_db_healthy_for, wsl_distro=distro),
            wait_ready=on(docker.wait_ready_for, wsl_distro=distro),
            world_output=on(_world_output, wsl_distro=distro),
            container_tail=on(_container_tail, wsl_distro=distro),
            selinux_enforcing=lambda: False,
            # Asked beside `selinux_enforcing` by the compose render whatever the
            # answer; left to default it ran `stat -f` on the HOST (found by the
            # end-to-end argv test). No SELinux, so no filesystem to ask about.
            fs_type=lambda _path: None,
            run_container=refused("Running an install container"),
            folder_claim=refused("Claiming a server folder for an extraction"),
            copy_from_image=refused("Copying templates out of an image"),
            exec_stdin=on(docker.exec_stdin, wsl_distro=distro),
            sql_query=on(docker.sql_query, wsl_distro=distro),
            volume_exists=on(docker.volume_exists, wsl_distro=distro),
            read_database=on(database_presence.take_reading, wsl_distro=distro),
            world_running=on(docker.world_running, wsl_distro=distro),
            db_running=on(docker.world_running, wsl_distro=distro),
            stop_db=on(docker.stop_containers, wsl_distro=distro),
            stop_world=on(docker.stop_containers, wsl_distro=distro),
            install_id=recorded_install_id,
            distro=distro,
        )


def _no_fingerprint(*_args: object, **_kwargs: object) -> None:
    """`Seams.context_fingerprint` for a server inside a WSL distro: no answer (T224, D4)."""
    return None


_INSTALL_ID = re.compile(rf"^[0-9a-f]{{{composegen.INSTALL_ID_LENGTH}}}$")


def recorded_install_id(server_dir: Path) -> str:
    """The install id `server_dir`'s record carries, for a server inside a WSL distro (T125).

    NOT recomputed: the folder's Windows spelling hashes to a different id from
    the Linux one Yu'lon recorded when it built the server inside the distro, and
    the images and compose project there are named after the recorded one. The
    record is `.yulon-install.json`, the same file every rebuild already refuses
    without; an id that is missing or not the shape this app writes is refused
    too, because a guess would compile images no compose file names.
    """
    state = read_state(server_dir, valid=())
    ident = state.install_id if state is not None else ""
    if not _INSTALL_ID.match(ident):
        raise InstallerError(
            f"{server_dir} has no usable install id in its {STATE_FILE} ({ident!r}), and the "
            "server inside the distro is named after that id. Nothing was started."
        )
    return ident


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

    _build_exit: int | None = None
    """The return code of the last `docker compose build` `stage_build()` ran (T225, cold
    review); None before a run started, and `CANCELLED_RETURNCODE` while one has not answered,
    so a run abandoned mid-compile counts as cancelled. `rebuild()` resets it and reads it:
    only a run that returned 0 or was cancelled can have moved the live tags, so only then
    does an unanswered image id count as a tag that moved."""

    def __init__(
        self,
        entry: CatalogEntry,
        *,
        installers_root: Path | None = None,
        import_probe: docker.ImportProbe | None = None,
        reset_unfinished: docker.ResetUnfinished | None = None,
        seams: Seams | None = None,
        database_snapshot: snapshot.DatabaseSnapshot | None = None,
    ) -> None:
        self.entry = entry
        self.installers_root = (
            installers_root if installers_root is not None else resources.installers_dir()
        )
        self._probe = import_probe
        self._reset = reset_unfinished
        self._seams = seams if seams is not None else Seams()
        # T217: the update route's copy of the databases its new build can change,
        # supplied by the caller for `import_probe`'s reason (`install_wiring`).
        self._snapshot = database_snapshot
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

    # -- the family's hooks around a press (T179) --------------------------

    def after_ready(self, server_dir: Path) -> Iterator[str]:
        """What a family starts once its server is up, after an install or a rebuild.

        Nothing on the spine. The TrinityCore family starts its movement maps
        here, in the background (T179 spec §1 step 10). Called after the last
        stage and before the press's closing line; what it yields is a sentence,
        and a family must not raise from it -- the server is up, and a press that
        made it so has not failed.
        """
        return iter(())

    def before_rebuild(
        self, server_dir: Path, route: str, press: str = server_build_presses.REBUILD
    ) -> Iterator[str]:
        """What a family must stop before `route` (a rebuild, an update, a return) changes it.

        Nothing on the spine. The TrinityCore family stops a running movement-map
        job here, whose output must not be written while the server is rebuilt
        (T179 spec §3). Called after the press's refusals and before its first
        change to the server; a stop that fails raises `InstallerError`, and the
        press stops there. `press` is the menu entry the refusal tells the player
        to press again.
        """
        return iter(())

    def lays_scripts_with_the_servers_down(self, server_dir: Path) -> bool:
        """Does a rebuild of this install lay files once the old world has stopped (T562)?

        False on the spine. The AzerothCore family's Lua scripts are read when the
        world starts, so they are laid in the window between the stop and the start,
        and a plain Rebuild then stops the servers in a call of its own, as the update
        route always did, for the window to exist.
        """
        return False

    def lay_scripts(self, server_dir: Path, *, quiet: bool) -> Iterator[str]:
        """Lay what `lays_scripts_with_the_servers_down()` says, with the servers stopped (T562).

        Called by `stage_recreate()` after the stop and before the start, and by the
        update route's put-back with the old sources back. Nothing on the spine.
        """
        return iter(())

    def check_moved_sources(
        self,
        server_dir: Path,
        moved: Sequence[tuple[EmulatorSource, Path, str]],
        *,
        to_pin: bool,
    ) -> Generator[str, None, object]:
        """The update route's family check of what the move brought, before the compile (T179).

        Called by `update_to_latest()` right after every source moved and before
        the carried patches, the movement-map stop and `rebuild()`: `moved` holds
        each source, its checkout and the commit it was on, and the checkout now
        stands on the commit it moved to. A refusal raises `InstallerError`, and
        the route puts every source back with nothing built and nothing written.
        What it returns is handed to `after_update()` once the rebuild is up.

        Nothing on the spine: every other family's database repositories stay on
        their pin (`held_at_its_pin()`), so a move brings no SQL with it.
        """
        yield from ()
        return None

    def after_update(
        self,
        server_dir: Path,
        changes: object,
        *,
        press: str,
        cancel: threading.Event | None,
    ) -> Iterator[str]:
        """What a family applies once the moved sources are built and running (T179).

        Called by `update_to_latest()` after `rebuild()` succeeded and the new
        commits were recorded, with what `check_moved_sources()` returned. Nothing
        on the spine. A failure raises `InstallerError`; the build and its sources
        stay, since the compile and the start both succeeded.
        """
        return iter(())

    def servers_down_work(
        self, server_dir: Path, changes: object, *, press: str
    ) -> ServersDownWork | None:
        """What the update route's rebuild does with its servers down (T179); None = nothing.

        Asked by `update_to_latest()` with what `check_moved_sources()` returned,
        after every refusal and before `rebuild()`. None on the spine, and then the
        rebuild's recreate is the single compose call it always was.
        """
        return None

    def databases_a_new_build_changes(self) -> tuple[Db, ...]:
        """The databases this family's new build can change at its first start, by role (T217).

        What the update route copies with the servers stopped, right before the new
        build first starts, and puts back if that build is rolled back
        (`snapshot.DatabaseSnapshot`). Empty on the spine: a family whose servers apply
        no update of their own at start has nothing a rollback must undo. Each family
        that has one says so with its evidence; `snapshot_databases()` resolves the
        roles to this entry's schema names.
        """
        return ()

    def databases_changed_but_not_copied(self) -> tuple[Db, ...]:
        """Databases the new build can change that the update does NOT copy, by role (T217).

        Said in the rollback's sentence, so a player is not told a database went
        back that did not. Empty on the spine.
        """
        return ()

    def snapshot_databases(self) -> tuple[str, ...]:
        """`databases_a_new_build_changes()` in this entry's own schema names (T217)."""
        return self._schema_names(self.databases_a_new_build_changes())

    def snapshot_left_out(self) -> tuple[str, ...]:
        """`databases_changed_but_not_copied()` in this entry's own schema names (T217)."""
        return self._schema_names(self.databases_changed_but_not_copied())

    def _schema_names(self, roles: Sequence[Db]) -> tuple[str, ...]:
        named = self.entry.schema_map()
        return tuple(named[role] for role in roles if role in named)

    def start_refusal(self, server_dir: Path, *, rebuilding: bool = False) -> str | None:
        """Why the server must not be started now, or None (T179).

        Asked by `stage_up()`, by `rebuild()` before anything (a press that would
        start the server and does not finish what stops it), and by the rollback
        before it starts the old build again. The spine's: `START_REFUSED_FILE`
        (T197 fix round 2), which a Rebuild (`rebuilding`) may clear for itself.
        TrinityCore's adds a world update left unfinished
        (`trinitycore.world_update_start_refusal`).
        """
        # T225 (live): with a stopped rebuild's build that may have landed since.
        return folder_start_refusal(server_dir, self._seams.image_id, rebuilding=rebuilding)

    def family_start_refusal(self, server_dir: Path) -> str | None:
        """The family's own reason no start may run, apart from `START_REFUSED_FILE` (T223).

        None in the spine. TrinityCore's is a world update left unfinished. Asked by
        the update route to decide whether a kept build's start is already refused
        by its family, which then lets its own finish start it.
        """
        return None

    # -- the contract ----------------------------------------------------

    def server_dir(self, options: InstallOptions) -> Path:
        """Where this install goes: what the user picked, or `default_server_dir()` under $HOME."""
        if options.server_dir is not None:
            return options.server_dir
        return default_server_dir(self.entry, Path.home())

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
        # Each stage first locks the folder to this account on Windows (T174),
        # so no secret lands in a folder anyone else can read.
        try:
            state = yield from self._staged(
                self._locking(self.stages(), server_dir, started_empty),
                ctx,
                run=ERROR_RUN_INSTALL,
            )
        except ReadyWaitStopped as stopped:
            # T247: the containers are up, and the world is still loading -- or,
            # stopped inside the watch after its banner, had reported ready. That
            # is what this Stop leaves, said before the Stop ends the press.
            if isinstance(stopped, StoppedInTheWatch):
                yield INSTALL_LEFT_RUNNING
            else:
                yield INSTALL_LEFT_LOADING
            raise
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
        # After the realm row, for `_advertise_realm()`'s reason: a background job
        # is a sentence, never a failed install (T179's movement maps).
        yield from self.after_ready(server_dir)
        # The bash path logs `install of <id> finished` (installer.py); this path
        # logged nothing at the end, so the only sign a run had ended was the
        # compose-project pin - which is how a tester on yulon-win11 (2026-08-28)
        # read a seven-minute readiness wait as "the install was not remembered".
        logger.info(f"install of {self.entry.id} finished")
        self._clear_error(server_dir, state)
        yield f"{self.entry.name} is installed and running in {server_dir}"

    def _staged(
        self, stages: Sequence[Stage], ctx: StageContext, *, run: str = ""
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
            self._record_error(ctx.server_dir, state, str(exc), run=run)
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
            f"{self.entry.name} has no {name} stage, so this app cannot rebuild it. "
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
        (`.notes/gates/tortoise-upgrade-m910q-2026-09-09/`): that install's
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
                f"{self.entry.name} cannot be updated safely: its update has no import "
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

    def repair_database_stages(self) -> tuple[Stage, ...]:
        """What Repair runs on a database Docker no longer has (T377): the install's own four.

        The database started (compose makes its volume again, empty), the
        family's import -- whose own probe reads `absent` there and imports the
        whole plan, as the install did -- the servers started and the wait for
        the world. Each is the family's own stage with its record taken away:
        the install already recorded them, and this press must not write a
        record of its own (`update_stages()`'s rule). AzerothCore puts its
        client-data download first, because that data lives in a volume too.
        """
        return tuple(
            replace(self.stage_named(name), recorded=False)
            for name in ("start-db", "import", "up", "ready")
        )

    def repair_database(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Make a database Docker no longer has again, from the server files, and start (T377).

        The Server tab offers it only after a Start or a Rebuild refused because
        the database was missing or empty, and the press asks Docker again
        before anything: it runs only when the answer is still `missing` or
        `empty`. A database that is there, or one nobody could ask, is never
        imported over -- the import's own probe would refuse a populated one,
        but a press that consented to "make it again" must not reach a database
        that holds anything.

        Raises:
            InstallerError: the database is there or could not be asked, a
                stage failed, or the press was cancelled.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        reading = self._seams.read_database(self.entry, server_dir)
        if reading.presence == "present":
            raise InstallerError(
                f"Docker has {self.entry.name}'s database again, with its login database in it, "
                f"so there is nothing to repair. Nothing was changed. Press Start."
            )
        if not reading.refuses:
            raise InstallerError(
                f"Yu'lon could not tell whether {self.entry.name}'s database is there "
                f"({reading.why or 'Docker did not say'}), so nothing was imported. Check that "
                f"Docker is running, then press Repair again."
            )
        yield f"Repairing {self.entry.name}'s database in {server_dir}"
        yield REPAIR_DATABASE_OPENING_NOTE
        self._check_cancel(cancel)
        ctx = replace(self._update_context(server_dir, cancel), updates_only=False)
        yield from self._staged(self.repair_database_stages(), ctx)
        yield (
            f"{self.entry.name}'s database was made again from the server files and the server "
            f"is running. If you have a backup, restore it on the Maintenance tab."
        )

    def _guard_then(
        self, stage: Stage, button: str, *, remedy: str = ""
    ) -> Callable[[StageContext], Iterator[str]]:
        """`stage`, with the world read once more immediately before its body runs.

        `button` is the label the refusal tells the user to press again; see
        `_refuse_writes_into_a_running_world()`. Both presses that wrap a stage
        in this wrap the one that writes, so the reading is as young as it can
        be made without asking inside the family's own body.
        """

        def run(ctx: StageContext) -> Iterator[str]:
            self._refuse_writes_into_a_running_world(button, remedy=remedy)
            yield from stage.run(ctx)

        return run

    def _refuse_writes_into_a_running_world(self, button: str, *, remedy: str = "") -> None:
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

        `remedy` replaces the running branch's "Press Stop" advice, for the one
        press whose own first step is the stop (T159): on the corrections press
        that advice does the wrong thing when followed -- the Server tab's Stop
        takes the database down too, and the banner goes with it.
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
            + (
                remedy
                or (
                    f"Press Stop on the Server tab, then press "
                    f'"{button}" again — the database is started '
                    f"on its own for it, and the world server stays down."
                )
            )
        )

    # -- corrected install-plan steps for an install already imported (T129) ---

    def correction_check(self, options: InstallOptions | None = None) -> CorrectionCheck:
        """Which of this install's SQL phases this version has corrected. NEVER raises.

        The reading behind the Server tab's banner. Every way of not knowing is
        a state rather than an exception, for `adopt_state()`'s reason: it is
        asked from a status path, and the one outcome that must never follow
        from a question nobody answered is a control that writes appearing.
        """
        server_dir = self.server_dir(options or InstallOptions())
        try:
            return self._correction_check(self._update_context(server_dir, None))
        except Exception as exc:  # noqa: BLE001 - a status path has nowhere to put one
            logger.warning(f"could not compare {server_dir}'s install plan with this app's: {exc}")
            return CorrectionCheck(
                "unreadable",
                why=f"this install's databases could not be asked ({type(exc).__name__}: {exc})",
            )

    def _correction_check(self, ctx: StageContext) -> CorrectionCheck:
        """The family's reading. The spine keeps no per-phase record, so it knows nothing."""
        return CorrectionCheck(
            "unmarked", why=f"{self.entry.name} keeps no record of its install-plan steps"
        )

    def correction_files(self, ctx: StageContext, phases: Sequence[str]) -> tuple[str, ...]:
        """The steps `phases` would stream into this install, named as the confirmation names them
        (a file by its path, a literal statement by its phase).

        Empty on the spine, `update_files()`'s reason; the CMaNGOS family expands
        its plan the same way its press does.
        """
        return ()

    def correction_confirmation(
        self, check: CorrectionCheck, options: InstallOptions | None = None
    ) -> str:
        """The dialog's text for `check` on this install, with the steps read off the folder.

        Raises:
            InstallerError: the phases could not be expanded against this
                folder; raised rather than listed as nothing, for
                `update_confirmation()`'s reason.
        """
        server_dir = self.server_dir(options or InstallOptions())
        files = self.correction_files(self._update_context(server_dir, None), check.offered)
        return corrections_confirmation(
            self.entry, server_dir, check.offered, files, check.withheld, check.stuck
        )

    def apply_corrections(
        self,
        check: CorrectionCheck,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Apply the corrected phases `check` offered, to an install already imported (T129).

        `check` is the reading the person confirmed; the family refuses the
        whole press unless the databases still read that way.

        `update_databases()`'s route with a different consent: the database
        started alone, then `import` with `ctx.corrections` set, which the family
        reads before `stage_import()` so neither import arm is reachable. The
        world is read before anything: a world that reads as running is STOPPED
        (T159, `_stop_the_world_for_corrections()` says why a refusal there was a
        dead end), and one that cannot be read is refused. It is read again right
        before the import stage, and that reading refuses on anything but an
        explicit `False` (owner answer 7). No marker is written; the family
        records each phase that landed. A world this press stopped is left
        stopped, and the last line says so.

        Raises:
            InstallerError: the entry's plan offers no phase this could apply,
                the world is unreadable, could not be stopped or is up again at
                the second reading, the offer no longer stands, the databases
                carry no marker, a step failed, or the press was stopped.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        if not correction_phases(self.entry) and not check.stuck:
            raise InstallerError(
                f"{self.entry.name}'s install plan marks no step as safe to apply again to a "
                f"server that already has data, so there is nothing for this to apply. Nothing "
                f"was started. That is a fact about this game's plan, not about your install."
            )
        yield f"Applying corrected install-plan steps for {self.entry.name} in {server_dir}"
        yield CORRECTIONS_OPENING_NOTE
        # `updates_only` is left as `_update_context()` sets it, True: see
        # `StageContext.corrections` for the family that ignores this field.
        ctx = replace(self._update_context(server_dir, cancel), corrections=check)
        # FIRST, before the database is started: `update_databases()`'s order,
        # for its reason -- a refused press leaves the stack as it found it.
        stopped = yield from self._stop_the_world_for_corrections(check, opts, ctx)
        self._check_cancel(cancel)
        again = f'Press "{CORRECTIONS_BUTTON_LABEL}" again: it stops the world server first.'
        stages = tuple(
            (
                replace(
                    stage,
                    recorded=False,
                    cancel_note=CORRECTIONS_CANCEL_NOTE,
                    run=self._guard_then(stage, CORRECTIONS_BUTTON_LABEL, remedy=again),
                )
                if stage.name == "import"
                else stage
            )
            for stage in (self.stage_named("start-db"), self.stage_named("import"))
        )
        yield from self._staged(stages, ctx)
        if stopped:
            yield (
                f"The world server was stopped for this and is still stopped. Press Start on "
                f"the Server tab to bring {self.entry.name} back up."
            )

    def _stop_the_world_for_corrections(
        self, check: CorrectionCheck, options: InstallOptions, ctx: StageContext
    ) -> Generator[str, None, bool]:
        """Owner answer 7 for the corrections press: stop the world rather than refuse (T159).

        Returns whether it stopped one. Until T159 this press refused a world
        that read as running, and on the Server tab that refusal could not be
        got past: the banner is taken each time the DATABASE comes up and
        dropped when it goes down, the Server tab's Stop takes the database
        down with the world, and a crash-looping world -- the Tortoise install
        whose honor maintenance truncates a missing table, the case T159 is
        about -- always reads as running (`docker.world_running()` counts
        `restarting` as up, and each cycle spends its first seconds `running`).
        So the stop is this press's own first step, and the dialog the person
        said Yes to says so.

        **Only an explicit running is stopped.** `None` is still the refusal it
        always was: a stop sent on "could not ask" is a guess about Docker.

        **The offer is read again BEFORE the stop**, and a press whose dialog no
        longer describes these databases is refused with the world left up. The
        family reads the record again anyway and refuses the same press; this
        reading exists so that the one thing such a press did is not stopping a
        server somebody is playing on. It is taken only on this branch, where
        the world is up and so is the database.

        **The stop is T158's**: `stop_containers(known=(spec,))` waits for a
        CMaNGOS world that is still loading -- it cannot hear SIGTERM until it
        has, and would be SIGKILLed mid-load at the end of the grace -- and
        stops at once a world that is not running (`restarting` is not), which
        is where a crash-looping one spends each backoff. The wait's sentences
        reach the panel as it waits (`_speaking`); the press's Cancel gives the
        stop up with nothing sent (`abandon`), and "Stop now anyway" forces it
        (`_stop_control(rollback=False)`, the rebuild's own reading of a Cancel
        before anything is touched). The by-name stop has a process deadline
        (`docker.STOP_PROCESS_DEADLINE_SECONDS`); past it the outcome is not
        known, and the refusal says the world may still be running.

        The world is not read again here after the stop: the import stage's own
        guard (`_guard_then`) is that reading, right before the first write, and
        a container that is back up by then is refused there with nothing sent.
        """
        container = self.entry.container_spec().world
        try:
            running: bool | None = self._seams.ask_world_running(container)
        except Exception:  # noqa: BLE001 - the guard below says it in words
            running = None
        if running is False:
            return False
        if running is None:
            # The spine's own refusal, words and all; it asks once more, and a
            # world that reads as down by then goes on to the import's guard.
            self._refuse_writes_into_a_running_world(CORRECTIONS_BUTTON_LABEL)
            return False
        if self.correction_check(options) != check:
            raise InstallerError(
                f"{self.entry.name}'s databases have changed since the confirmation was shown, so "
                f"there may be nothing left for this to apply. Nothing was stopped and nothing "
                f"was applied: the world server is still running. Press Refresh on the Server "
                f"tab and look again."
            )
        yield (
            f"The world server ({container}) is running or restarting; stopping it before "
            f"anything is written. It saves as it does on Stop, which can take a few minutes on "
            f"a populated world."
        )
        spec = self.entry.container_spec()
        # A world that restarts while it is waited on is the crash loop this press
        # exists to end, and it never finishes loading (T159, the live gate).
        control = replace(_stop_control(ctx, rollback=False), restart_ends_the_wait=True)

        def stop_it(say: docker.OutputSink) -> None:
            self._seams.stop_world(
                [container],
                known=(spec,),
                control=replace(control, say=say),
                deadline=docker.STOP_PROCESS_DEADLINE_SECONDS,
            )

        try:
            yield from _with_hint(_speaking(stop_it, control.abandon), CORRECTIONS_WAIT_HINT)
        except docker.SaveAbandoned as exc:
            # T384: the world heard the stop and is saving; Yu'lon stopped watching.
            raise InstallStopped(
                f"This was stopped while {self.entry.name}'s world server was saving its "
                f"characters on the way down, so nothing was applied. The world server closes "
                f"by itself once the saves are written."
            ) from exc
        except docker.SaveFirstAbandoned as exc:
            raise InstallStopped(
                f"This was stopped while {self.entry.name}'s world server was saving its "
                f"characters, before it was told to stop, so nothing was applied. It is still "
                f"running."
            ) from exc
        except docker.StopAbandoned as exc:
            raise InstallStopped(
                f"This was stopped while {self.entry.name}'s world server was still loading, so "
                f"the world server was not stopped and nothing was applied. It is still running."
            ) from exc
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"Yu'lon could not stop {self.entry.name}'s world server ({exc}). Nothing was "
                f"applied, and the world server may still be running. Check that Docker is "
                f'answering, then press "{CORRECTIONS_BUTTON_LABEL}" again.'
            ) from exc
        yield "The world server is stopped."
        return True

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

    def write_import_marker(
        self, ctx: StageContext, landed: Sequence[SqlPhase] | None = None
    ) -> None:
        """Write the completion marker for this install, through the family's own writer.

        `landed` is what an import applied whole, recorded beside the marker
        (T129); the adopt press passes nothing, and no phase record is written.

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
            f"Writing one row into {row.schema}.{row.table}: this install plan "
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
                f"{self.entry.name} cannot be adopted safely: its adoption has no adopt "
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

    def _wait_for_docker(
        self, ctx: StageContext, *, saying: str, stoppable: bool
    ) -> Generator[str, None, float | None]:
        """Ask Docker until it answers, for up to `DOCKER_PATIENCE_S` (T223).

        Returns None when Docker answered, else the seconds spent asking -- which is
        what the refusal says, because a duration a user reads is one that was
        measured (`_spell_elapsed()`). Says `saying` when the first ask goes
        unanswered and one sentence when Docker answers late; silent when it
        answers at once.

        **Bounded twice, by the clock and by the number of asks.** The clock is the
        bound that holds in use, where each ask can take its own 10 seconds; the
        count is the one that holds where `sleep` costs nothing and the clock is
        real -- every test -- and without it this would spin real seconds. No ask
        STARTS once the three minutes are spent, and the last pause is cut to what
        is left; an ask started inside them can still run to its own 10-second
        bound, so the wait can end up to that much past three minutes, and the
        refusal says the time it measured.

        **`stoppable`: a Stop ends the wait before the replace, and does not end it
        in a restore.** Before the replace a Stop ends the press with no container
        replaced, and the restore keeps the finished build (T224), which is
        `_stop_control()`'s `abandon`. In a restore the Stop is what may have
        STARTED it, and `_stop_control()` makes a Cancel there force the failed
        build's stop rather than give up, because a restore abandoned half-way
        leaves the tags or the servers half-way; reading the Stop would end the
        restore's wait on its first ask, so `saying` tells the player Stop waits.
        A Stop is read between pauses of a second (`_pause()`) and around each ask,
        so it lands within a second -- or, during an ask, when the ask returns,
        which is at most its own 10 seconds.
        """
        stop = ctx.cancel if stoppable else None
        started = self._seams.monotonic()
        if self._seams.docker_ready():
            return None
        yield saying
        for _ in range(int(DOCKER_PATIENCE_S // DOCKER_PATIENCE_POLL_S)):
            left = DOCKER_PATIENCE_S - (self._seams.monotonic() - started)
            if left <= 0:
                break
            self._pause(min(DOCKER_PATIENCE_POLL_S, left), stop)
            # Both read again after the pause and BEFORE the ask, which can take its
            # own 10 seconds: a Stop pressed during the pause, and a pause that used
            # up the last of the three minutes (Codex review, both passes).
            self._stopped_waiting_for_docker(stop)
            if self._seams.monotonic() - started >= DOCKER_PATIENCE_S:
                break
            answered = self._seams.docker_ready()
            self._stopped_waiting_for_docker(stop)
            if answered:
                yield (
                    f"Docker answered after {_spell_elapsed(self._seams.monotonic() - started)}."
                )
                return None
        return self._seams.monotonic() - started

    def _stop_before_replace(self) -> str:
        """What a Stop costs in the recreate's wait for Docker (T223, T224 D1 and D4)."""
        if self._seams.distro is not None:
            return "Stop gives up the new build and leaves the server as it is."
        return (
            "Stop leaves the server as it is and keeps the finished build: a later rebuild uses "
            "it instead of compiling, if the server folder has not changed since."
        )

    def _pause(self, seconds: float, stop: threading.Event | None) -> None:
        """Sleep `seconds` a second at a time, ending early once `stop` is set (T223)."""
        left = seconds
        while left > 0 and not (stop is not None and stop.is_set()):
            step = min(1.0, left)
            self._seams.sleep(step)
            left -= step

    def _stopped_waiting_for_docker(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            # Only the Stop: the restore that follows says what was and was not
            # replaced, and saying it here too said it twice (cold review).
            raise InstallStopped("The rebuild was stopped while it waited for Docker.")

    def stage_recreate(
        self,
        ctx: StageContext,
        *,
        before_replace: Callable[[], None] | None = None,
        rollback: bool = False,
        servers_down: ServersDownWork | None = None,
    ) -> Iterator[str]:
        """Replace the long-running containers so the binary just built is the one running.

        **With `servers_down` (T179's update route) the stop and the start are two
        calls**, and the family's work runs between them: `prepare()` before
        anything stops, the servers stopped (`stop_servers`, with T158's load wait
        and `before_replace` as its `before_signal`), `forward()`, then the same
        recreate as always -- its own stop finds nothing running. Without it, and on
        a rollback (whose servers `_restore_rollback()` has already stopped), the
        one `recreate` call below. **A family that lays files for the world's start
        (`lays_scripts_with_the_servers_down()`, T562) takes the two calls on a plain
        Rebuild as well**, and lays between them.

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
        # T223: a wait, not one ask -- see `_wait_for_docker()`.
        waited = yield from self._wait_for_docker(
            ctx,
            saying=(
                "Docker did not answer. Waiting up to 3 minutes for Docker before starting the "
                "build from before this rebuild again. Stop does not end this wait: the build "
                "from before is being put back, and it is waited for either way."
                if rollback
                else "Docker did not answer. The new build is finished; waiting up to 3 minutes "
                f"for Docker before replacing the containers. {self._stop_before_replace()}"
            ),
            stoppable=not rollback,
        )
        if waited is not None and rollback:
            # Its servers were stopped by the restore before the tags moved back
            # (`ROLLBACK_STOPPING`), so they are left stopped, on the old build.
            raise _DockerSilentForRestart(
                f"Docker did not answer for {_spell_elapsed(waited)}, so the build from before "
                f"this rebuild is back on its tags but was not started: its servers are "
                f"stopped. Once Docker answers, press Start."
            )
        if waited is not None:
            # What was and was not replaced, and the press, are the restore's to say
            # (`_restore_rollback()`), or T170's when nothing was kept: said here too,
            # they were said twice (cold review).
            raise InstallerError(
                f"Docker did not answer for {_spell_elapsed(waited)} after the build finished. "
                f"Check that Docker is running."
            )
        yield (
            "Replacing the containers so the build from before this rebuild is what starts."
            if rollback
            else "Replacing the running containers so the new build is what starts."
        )
        warned = self._put_back_the_zone_file(ctx.server_dir)
        if warned is not None:
            yield warned
        warned = self._refresh_world_data(ctx.server_dir)
        if warned is not None:
            yield warned
        spec = self.entry.container_spec()
        # The replace begins with a stop, and a world still loading cannot hear
        # it (T158). `recreate_staged()` waits for it right before that stop --
        # the one check, with nothing between it and the signal -- and runs on
        # a thread here so the wait's sentences reach the panel as it waits.
        # `before_replace` is its `before_signal`: past it, something may have
        # been touched; a Cancel before it leaves nothing touched.
        control = _stop_control(ctx, rollback=rollback)
        stop_first = servers_down is not None or self.lays_scripts_with_the_servers_down(
            ctx.server_dir
        )
        if stop_first and not rollback:
            if servers_down is not None:
                yield from servers_down.prepare()

            def stop_them(say: docker.OutputSink) -> None:
                self._seams.stop_servers(
                    spec,
                    ctx.server_dir,
                    control=replace(control, say=say),
                    before_signal=before_replace,
                )

            try:
                yield from _with_hint(_speaking(stop_them, control.abandon), REBUILD_WAIT_HINT)
            except docker.SaveAbandoned as exc:
                raise InstallStopped(
                    "The rebuild was stopped while the world server was saving its characters "
                    "on the way down, before the new build replaced it."
                ) from exc
            except docker.SaveFirstAbandoned as exc:
                raise InstallStopped(
                    "The rebuild was cancelled while the world server was saving its characters, "
                    "before it was told to stop, so its containers were not replaced -- the "
                    "server you have is still the one that was running before this rebuild."
                ) from exc
            except docker.StopAbandoned as exc:
                raise InstallStopped(
                    "The rebuild was cancelled while the world was still loading, so its "
                    "containers were not replaced -- the server you have is still the one that "
                    "was running before this rebuild."
                ) from exc
            except docker.DockerCommandError as exc:
                raise InstallerError(
                    f"The server was rebuilt, but its servers could not be stopped to start the "
                    f"new build: {exc}"
                ) from exc
            # T562: the scripts the world reads at its start, laid now that nothing runs.
            yield from self.lay_scripts(ctx.server_dir, quiet=servers_down is None)
            if servers_down is not None:
                yield from servers_down.forward(ctx)

        # T577: marked offline before the replace starts the new world, so the realm list says
        # Offline for the whole load. On the update route the old world is already down here,
        # and the bit is set within seconds of that. Best effort, never raising; a replace
        # given up or refused with the old world still running takes the bit off again below.
        self._seams.mark_realm_offline(self.entry, spec, ctx.server_dir)

        def replace_them(say: docker.OutputSink) -> bool:
            return self._seams.recreate(
                spec,
                ctx.server_dir,
                control=replace(control, say=say),
                before_signal=before_replace,
            )

        try:
            try:
                yield from _with_hint(
                    _speaking(replace_them, control.abandon),
                    ROLLBACK_WAIT_HINT if rollback else REBUILD_WAIT_HINT,
                )
            except Exception:
                # T577: a replace given up or refused may leave the old world running behind
                # the bit set above, and only a start clears it.
                self._seams.clear_realm_offline(self.entry, spec, ctx.server_dir)
                raise
        except docker.SaveAbandoned as exc:
            raise InstallStopped(
                "The rebuild was stopped while the world server was saving its characters on "
                "the way down, before the new build replaced it."
            ) from exc
        except docker.SaveFirstAbandoned as exc:
            raise InstallStopped(
                "The rebuild was cancelled while the world server was saving its characters, "
                "before it was told to stop, so its containers were not replaced -- the server "
                "you have is still the one that was running before this rebuild."
            ) from exc
        except docker.StopAbandoned as exc:
            raise InstallStopped(
                "The rebuild was cancelled while the world was still loading, so its "
                "containers were not replaced -- the server you have is still the one that was "
                "running before this rebuild."
            ) from exc
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
        missing_images_ok: bool = False,
        servers_down: ServersDownWork | None = None,
        press: str = server_build_presses.REBUILD,
    ) -> Iterator[str]:
        """Recompile this install and restart it on what was compiled. Yields output live.

        `press` is the "Server build ▾" entry that started this, named where a sentence
        says what pressing it again does (T223); the update route passes its own.

        `servers_down` is the update route's (T179): what the family does between
        the recreate's stop and its start, and again in a rollback's window before
        the old build starts (`ServersDownWork`). A Rebuild press passes none.

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

        **Every transient name this makes, and every way out (T79).** Two names
        per image in `built_image_refs()` -- four images on an AzerothCore
        install, so up to eight names: `<ref>-rollback` before the compile
        (`_keep_rollback`), and `<ref>-failed` only if a restore starts
        (`_restore_rollback`). A `-rollback` name must not outlive the press:

        * **the rebuild finished** -- the `-rollback` names are released here,
          and no `-failed` name was ever made;
        * **`_keep_rollback` refuses** -- the names it had already made are
          released before it raises;
        * **a stage failed before the compile finished** -- released; the live
          tags never moved, so they were duplicates;
        * **...the compile was stopped or abandoned, or a stopped build's record is
          there (scoped re-review)** -- KEPT, here and in every restore, with
          `STOPPED_BUILD_FILE` (T225, measured live: BuildKit moved the live tag 13
          minutes after a Stop): every Start compares the tags with them, and the
          next Rebuild, Update or Return settles them (`_settle_stopped_build()`)
          and the record goes only once that press succeeds;
        * **the world came up and then stopped** (`WorldStoppedAfterReadyError`,
          T71's keep) -- released, and the new build keeps the live names;
        * **a stage failed after the compile** -- `_restore_rollback` puts the
          old build back and releases both sets, its `-failed` names after the
          recreate that frees them (see `FAILED_TAG_SUFFIX`);
        * **...and the old build did not come up either** -- released too, which
          is the exit that kept them until T79;
        * **...and docker refused to NAME or to MOVE a tag** -- the `-rollback`
          names are KEPT, deliberately: the restore did not happen, so they are
          the only copy of the old build there is, and the sentence the user
          reads says exactly that. That exit, and a stop of the new build's
          servers that failed, raise `RollbackNotDone` (T197), so the update
          route leaves its sources with the build the tags still name;
        * **anything that is not an `InstallerError`** -- released if no compile
          finished, kept and logged if one did.

        A release is `_let_go()`, which reads the daemon's refusal and asks again
        with `-f` where the only thing in the way is a stopped container -- the
        leak the round-3 gate found, and the reason the table above was written
        down rather than assumed.

        **With the images gone there is no rollback to keep (T170).** Names
        gone is not always that: when this install's containers still hold a
        whole build by image id, `_keep_rollback()` keeps THAT, and the press
        is an ordinary one (round 2). With no such build it is refused by
        default, as owner answer 2 has it. `missing_images_ok` is the
        "Rebuild the server…" press's own answer, given because its
        confirmation says, before the player agrees, that a server whose images
        are gone is compiled without one (`installer.rebuild_confirmation()`;
        the owner's decision of 2026-09-28). Then `_keep_rollback()` keeps
        nothing and says so, and every failure says what it leaves: a compile
        that fails or is stopped leaves the containers as they were and the
        images still missing, and a new build that does not come up stays on
        the containers with nothing to put back. "Update the server to
        latest…" does not pass it, so it still refuses there -- its
        confirmation promises the build you have keeps running.

        Raises:
            InstallerError: any refusal (see `_refuse_unless_rebuildable`), any
                stage that failed, or a cancel. The message is the sentence a
                user reads.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        state = self._refuse_unless_rebuildable(server_dir)
        if servers_down is None or not servers_down.finishes_start_refusal:
            # T179: a rebuild ends in a start, and this press does not finish what
            # refuses one (the update route's `servers_down` does, when it says so).
            refused = self.start_refusal(server_dir, rebuilding=True)
            if refused is not None:
                raise InstallerError(f"{refused} Nothing was changed.")
        # T377: a rebuild ends in a start, and a start on a database Docker no
        # longer has puts the server on a new, empty one. Asked before an hour of
        # compiling; `unknown` goes on, as a Start does.
        reading = self._seams.read_database(self.entry, server_dir)
        if reading.refuses:
            sentence = database_presence.sentence_for(reading.presence)
            raise InstallerError(f"{sentence} Nothing was changed.")
        # T217 (B): a plain Rebuild compiles the folder as it is, so a source that is
        # not on the commit the running build was made from would compile a mix of
        # two versions. The update route moves them together and passes its work.
        unchecked = (
            self._refuse_sources_off_their_build(server_dir, state) if servers_down is None else ()
        )
        planned = self.rebuild_stages()
        renders = any(stage.name == DOCKERFILE_STAGE for stage in planned)
        # Read BEFORE the first stage and only for the families that have one,
        # because the promise in the opening note is about this: a press stopped
        # before the containers are replaced leaves the server exactly as it is,
        # and a rewritten recipe left on disk is not "exactly as it is" -- the
        # next build would compile something the user never confirmed.
        ground = self._recipe_ground(server_dir) if renders else {}
        yield f"Rebuilding {self.entry.name} in {server_dir}"
        yield rebuild_opening_note(
            renders_dockerfile=renders, parked=read_parked_build(server_dir) is not None
        )
        yield from unchecked
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
        parking = _Parking()
        self._build_exit = None

        def may_have_tagged() -> bool:
            """Whether this press ran something that moves the live tags (T225, cold review).

            A build run that returned 0 or was cancelled (compose may have tagged before
            the Stop killed it), or the kept build being put on the tags. A run that
            failed otherwise, or none at all, tagged nothing it finished, so an id Docker
            does not give is read as "not moved" there.
            """
            return parking.tagging or self._build_exit in (0, docker.CANCELLED_RETURNCODE)

        def build(stage_ctx: StageContext) -> Iterator[str]:
            nonlocal built
            # T224: F0, after `write-dockerfile` rendered the recipe this compiles.
            parking.fingerprint = self._seams.context_fingerprint(server_dir, refs=refs)
            used = yield from self._use_or_clear_the_kept_build(server_dir, refs, kept, parking)
            if used:
                parking.after = parking.fingerprint
            else:
                try:
                    yield from self.stage_build(stage_ctx)
                except InstallerError:
                    # T225: a Stop or a failure after compose tagged can still keep it.
                    if self._compose_tagged(refs, kept, unanswered_moved=may_have_tagged()):
                        parking.after = self._seams.context_fingerprint(server_dir, refs=refs)
                        parking.made_unix = int(time.time())
                    raise
                parking.after = self._seams.context_fingerprint(server_dir, refs=refs)
                parking.made_unix = int(time.time())
            built = True

        def recreate(stage_ctx: StageContext) -> Iterator[str]:
            def mark_touched() -> None:
                nonlocal touched
                touched = True

            # `touched` flips inside `stage_recreate()`, synchronously, immediately
            # before the compose command is issued -- not at the stage's first yield
            # (round 2), which left a window between the readiness probe and the
            # command where a failure read as a partial replacement.
            yield from self.stage_recreate(
                stage_ctx, before_replace=mark_touched, servers_down=servers_down
            )

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
        def ready(stage_ctx: StageContext) -> Iterator[str]:
            # T247 review: a Stop here lets the new world finish loading before
            # the rollback stops it (T158), instead of ending the wait at once.
            yield from self.stage_ready(stage_ctx, stop_lets_it_load=True)

        wrappers: dict[str, Callable[[StageContext], Iterator[str]]] = {
            "build": build,
            "recreate": recreate,
            "ready": ready,
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
                f"{sorted(wrappers)[0]} stage to watch, so a failure could not be rolled "
                f"back. That is a bug in this build, not something you did. Nothing was "
                f"started."
            )
        # After every refusal above and before the first thing this press changes
        # (the rollback tags): a family's background job over the server's files
        # must not run while it is rebuilt (T179's movement maps). A stop that
        # fails raises, and nothing was started.
        yield from self.before_rebuild(server_dir, "the rebuild")
        refs = self.built_image_refs(ctx)
        # T225 (live): a build an earlier Stop left running may have landed since.
        yield from self._settle_stopped_build(server_dir, refs)
        hold = read_stopped_build(server_dir) is not None
        kept = yield from self._keep_rollback(ctx, refs, missing_ok=missing_images_ok)
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
            failure = str(exc)
            if not built and self._compose_tagged(refs, kept, unanswered_moved=may_have_tagged()):
                # T225: compose moves the live tags as its build finishes, and a
                # Stop read after that -- or one that killed compose after it
                # tagged -- raised before `built` was set. The tags are read off
                # the daemon instead: a restore puts them back (a tag that never
                # moved is moved back onto the image it already names), and the
                # `-rollback` names stay until it has.
                built = True
                if cancel is not None and cancel.is_set():
                    failure = STOPPED_AS_THE_BUILD_FINISHED
            if not built and kept and self._build_exit == docker.CANCELLED_RETURNCODE:
                # T225 (seen live on Windows 11): a stopped compile can still land 13 minutes
                # later, so the `-rollback` names stay, whatever the ids say now.
                kept_note = self._remember_the_stopped_build(server_dir, refs, parking)
                message = f"{exc} {STOPPED_BUILD_NOTE}{kept_note}"
                self._record_error(server_dir, ctx.state, message)
                raise InstallerError(message) from exc
            if not built and kept and hold:
                # An earlier stop's build may still land: its record and the names stay.
                message = f"{exc} {STOPPED_EARLIER_NOTE}"
                self._record_error(server_dir, ctx.state, message)
                raise InstallerError(message) from exc
            if not built:
                # A compile that failed leaves the live tags on the build that is
                # running: the second name is a duplicate.
                yield from self._release(kept)
                if kept:
                    raise
                # T170: no rollback was kept, because there was no build to
                # keep. Nothing was replaced, so nothing needed putting back.
                message = f"{exc} {NO_ROLLBACK_NOT_BUILT}"
                self._record_error(server_dir, ctx.state, message)
                raise carry_detail(exc, InstallerError(message)) from exc
            if isinstance(exc, WorldStoppedAfterReadyError):
                # The owner's answer, 2026-09-16: keep the new build and report
                # the abort. The compile finished, the containers were replaced,
                # and the server that came out of it STARTED — it then stopped
                # for a reason on the data side of the binary (T63: a module's
                # db-world SQL that was never applied), and putting an hour of
                # correct compiling back does not create the missing table. The
                # rollback tags are let go exactly as the success path lets them
                # go, so the new images keep the live names.
                #
                # Every PRE-banner verdict still rolls back below: a build whose
                # server never came up at all is a build worth putting back, and
                # that is the whole of what this subclass separates.
                yield from self._release(kept)
                # The new build started: as a success, the stopped build's record goes.
                also = self._forget_the_stopped_build(server_dir)
                kept_build = (
                    f"{exc} The build from this rebuild was KEPT and is what the containers "
                    f"are running: the compile finished and the server it made did start, so "
                    f"there is nothing wrong with the build to undo. Fix what stopped it and "
                    f"press Start.{also}"
                )
                self._record_error(server_dir, ctx.state, kept_build)
                raise carry_detail(exc, WorldStoppedAfterReadyError(kept_build)) from exc
            if not kept:
                # T170: the compile finished with no build from before to go
                # back to. `touched` says whether the containers run it yet.
                # No start refusal here (owner, 2026-09-28; lead, T223): a first
                # build has no old one to go back to, so one would leave nothing runnable.
                message = f"{failure} {NO_ROLLBACK_BUILT if touched else NO_ROLLBACK_UNTOUCHED}"
                self._record_error(server_dir, ctx.state, message)
                if touched:
                    raise carry_detail(exc, RebuildChangedTheServer(message, up=False)) from exc
                raise carry_detail(exc, InstallerError(message)) from exc
            message = yield from self._restore_rollback(
                ctx,
                refs,
                kept,
                touched,
                failure,
                servers_down=servers_down,
                press=press,
                parking=parking,
                # A touched press's build started, so an earlier stop's record no longer
                # describes the server (scoped re-review, the lead's (a)): it goes below,
                # and the restore treats the names as it always did.
                hold_rollback=hold and not touched,
            )
            also = self._forget_the_stopped_build(server_dir) if touched and hold else ""
            message_said = f"{message}{also}"
            self._record_error(server_dir, ctx.state, message_said)
            if isinstance(message, _LeftStopped):
                raise carry_detail(exc, ServersLeftStopped(message_said)) from exc
            if isinstance(message, _NotStopped):
                raise carry_detail(exc, OldBuildNotStopped(message_said)) from exc
            if isinstance(message, _NotPutBack):
                # T197: the tags still name the new build (or are mixed), so the
                # update route must not put the old sources back under it.
                # Fix round 2: mixed tags are refused by every start until a
                # Rebuild succeeds -- in this geometry `compose up -d` would run
                # the new import image beside the old world server.
                warned = owe_start(server_dir) if message.mixed else ""
                # Written on every untested exit; on the update route, taken back
                # after `keep()` where the family's own record refuses Start instead
                # (the lead's option 1, scoped re-review: judged by the record, not a flag).
                if message.untested:
                    # T223 (cold review, then the lead): owner answer D1 -- a new build
                    # that never started is never what the next Start runs, whether
                    # Docker went silent or refused a tag. The update route says the
                    # refusal in its own note (`untouched_note()`); a Rebuild press here.
                    warned = owe_start(server_dir, why=UNTESTED_BUILD)
                    if not warned and servers_down is None:
                        warned = (
                            "Start is refused until this server is rebuilt: press "
                            f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}."
                        )
                raise carry_detail(
                    exc,
                    RollbackNotDone(
                        f"{message_said} {warned}" if warned else message_said,
                        touched=message.touched,
                        mixed=message.mixed,
                    ),
                ) from exc
            if touched and isinstance(message, _PutBackWhole) and isinstance(exc, ReadyWaitStopped):
                # The lead's ruling: Stop during the load, and a clean rollback.
                raise StoppedAndPutBack(str(message), up=True) from exc
            if touched:
                # The old build is back, running or not, on whatever the new one
                # wrote into the database: `_restore_rollback()` says so.
                raise carry_detail(
                    exc,
                    RebuildChangedTheServer(message_said, up=not isinstance(message, _NotUpEither)),
                ) from exc
            raise carry_detail(exc, InstallerError(message_said)) from exc
        except BaseException:
            # NOT a refusal this method has an answer for: a bug in a stage, a
            # `KeyboardInterrupt`, or a consumer that stopped reading (which
            # arrives here as `GeneratorExit`). Until T79 the rollback names
            # simply stayed on the daemon, because `except InstallerError` is
            # the only handler there was and every release lives inside it.
            #
            # Nothing may be YIELDED here -- a `GeneratorExit` handler that
            # yields raises `RuntimeError: generator ignored GeneratorExit` and
            # would replace the real failure with that one -- so this path is
            # silent in the log panel and loud in the log file.
            #
            # `built` decides, and it decides the same way the branch above
            # does: with no compile finished the live tags still name the build
            # that is running and the rollback names are duplicates, so they go.
            # Once a compile HAS finished, those names are the only copy of the
            # old build there is, and an unknown failure is the worst moment to
            # throw it away -- they are kept, and the log says where they are.
            if not built and not self._compose_tagged(
                refs, kept, unanswered_moved=may_have_tagged()
            ):
                if kept and (self._build_exit == docker.CANCELLED_RETURNCODE or hold):
                    # T225 (live): abandoned mid-compile, or an earlier stop's record is
                    # there; a solve can still land.
                    note = (
                        self._remember_the_stopped_build(server_dir, refs, parking)
                        if self._build_exit == docker.CANCELLED_RETURNCODE
                        else ""
                    )
                    logger.error(
                        f"rebuild of {self.entry.id} was abandoned mid-compile; the build from "
                        f"before it stays on the daemon as {', '.join(kept)}.{note}"
                    )
                else:
                    self._let_go(kept)
            elif kept:
                if touched and hold:
                    # The lead's (a): this press's build started; logged, never yielded.
                    self._forget_the_stopped_build(server_dir)
                # T225: also when compose had moved the tags before `built` was set
                # (a consumer that stopped reading at "The build finished.").
                logger.error(
                    f"rebuild of {self.entry.id} ended unexpectedly after the compile; the "
                    f"build from before it is still on the daemon as {', '.join(kept)}"
                )
                # Cold review (lead's decision): nothing here can put the tags back,
                # since that would yield, so no Start may run the untested build they
                # name. A file write, not a yield; only a Rebuild that succeeds clears it.
                warned = owe_start(server_dir, why=UNTESTED_BUILD)
                if warned:
                    logger.error(warned)
            else:
                logger.error(
                    f"rebuild of {self.entry.id} ended unexpectedly after the compile; no build "
                    "from before it was kept, because its images were not all there (T170)"
                )
            raise
        yield from self._release(kept)
        # T225: only a press that succeeded settles a stopped build's record.
        stuck = self._forget_the_stopped_build(server_dir)
        if stuck:
            yield stuck.strip()
        logger.info(f"rebuild of {self.entry.id} finished")
        self._clear_error(server_dir, state)
        left = forget_owed_start(server_dir)
        if left:
            yield left
        # T217: the build now running was made from the folders as they are.
        forget_sources_off(server_dir)
        yield from self.after_ready(server_dir)
        yield REBUILD_CLOSING_NOTE
        yield f"{self.entry.name} was rebuilt and is running in {server_dir}"

    def remove_kept_build(self, options: InstallOptions | None = None) -> str:
        """Remove the kept build now: "Remove kept build…" on the Server tab (T224, D3).

        Every `<ref>-parked` name is let go (`_let_go()`, whose `-f` covers a stopped
        container), and only when none is left is the record forgotten: a name Docker
        kept, or a Docker that did not answer, leaves the build recorded and the
        banner up (Codex adversarial review). `remove_image()` reads "no such image"
        as done. Returns the sentence; never raises for Docker's refusals.
        """
        server_dir = self.server_dir(options or InstallOptions())
        names = [ref + PARKED_TAG_SUFFIX for ref in self.image_refs_at(server_dir)]
        left = self._let_go(names)
        if left:
            return (
                f"The kept build was not removed: Docker would not remove {', '.join(left)} (the "
                f"log says why). It is still kept; press {REMOVE_KEPT_BUILD_LABEL} again once "
                f"Docker answers."
            )
        forgot = forget_parked_build(server_dir)
        if forgot:
            return (
                f"The kept build was removed from Docker, but {forgot}; the next rebuild "
                f"forgets it."
            )
        return "The kept build was removed."

    # ------------------------------------------------------- update to latest (T64)

    def upstream_news(
        self, options: InstallOptions | None = None, *, now: int | None = None
    ) -> upstream.UpstreamNews:
        """How far upstream is past what each moving source was built from (T124). Never raises.

        Each source's cached row while it is fresh (`upstream.read_cached()`),
        which is what keeps a tab opened ten times a day -- or a Refresh pressed
        ten times -- to one set of requests. A source with no fresh row is read
        for its HEAD through the read-only container, and GitHub is asked how
        far its branch is past that HEAD; the answers are kept beside the
        install record.

        Only the sources `sources_that_move()` names. A `*-db` repository stays
        on its pin through "Update the server to latest…", so counting its new
        commits would advertise code that button does not bring in.

        HEAD, not the catalog pin, because the pin is what THIS build of the app
        would install, and an install made before a pin moved (T120 moved
        TortoiseBots') is still on the old one.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        moving = self.sources_that_move()
        clock = upstream.now_unix() if now is None else now
        keys = [(source.repo, source.follow) for source in moving]
        fresh = upstream.read_cached(server_dir, keys, clock)
        found: list[upstream.SourceNews] = []
        asked = False
        record = read_state(server_dir, valid=())
        for index, source in enumerate(moving):
            kept = fresh.get(source.repo)
            if kept is not None:
                found.append(kept)
                continue
            asked = True
            label = "server" if index == 0 else source.repo.rsplit("/", 1)[-1]
            stands, release = self._behind(server_dir, source, refused=_refused_for(record, source))
            found.append(
                upstream.SourceNews(
                    repo=source.repo,
                    label=label,
                    behind=None if stands is None else stands.ahead,
                    rewritten=stands.behind if stands is not None and stands.diverged else 0,
                    checked_unix=clock,
                    follow=source.follow,
                    release=release,
                    installed=_installed_release(record, source.repo),
                )
            )
        taken = clock if asked or not found else min(row.checked_unix for row in found)
        news = upstream.UpstreamNews(checked_unix=taken, sources=tuple(found))
        if asked and (server_dir / STATE_FILE).is_file():
            upstream.write_cached(server_dir, news)
        return news

    def _behind(
        self, server_dir: Path, source: EmulatorSource, *, refused: str = ""
    ) -> tuple[upstream.Comparison | None, str]:
        """How upstream stands to one source and, for a releases source, the newest release.

        `(None, "")` when its HEAD or GitHub could not be asked. A source that
        follows releases is compared with the newest release's commit rather
        than the branch tip, so a checkout already on (or past) that release
        answers 0 however far the branch has moved on. The WHOLE comparison is
        returned, because a history upstream rewrote is ahead and behind at once.
        """
        slug = upstream.github_slug(source.repo)
        if slug is None:
            return None, ""
        head = self._seams.head_sha(server_dir / source.dest)
        if head is None:
            return None, ""
        get = self._seams.upstream_get
        if source.follow == "releases":
            release = upstream.newest_release(slug, get=get)
            if release is None:
                return None, ""
            return upstream.compare(slug, head, release.sha, get=get), release.tag
        branch = source.branch or "HEAD"
        if refused:
            # T179: an update refused this exact commit. While upstream's branch
            # is still on it (nothing past it), there is nothing to offer: the
            # press would refuse again. A commit past it is offered as usual.
            past = upstream.compare(slug, refused, branch, get=get)
            if past is not None and past.ahead == 0 and past.behind == 0:
                return upstream.Comparison(ahead=0, behind=0, status="identical"), ""
        return upstream.compare(slug, head, branch, get=get), ""

    def sources_that_move(self) -> tuple[EmulatorSource, ...]:
        """Which of this entry's sources an update to latest moves. The `*-db` ones do not.

        **A database repository is not code and must not follow this button.**
        `src/tbc-db` and `src/classic-db` are the world databases: their
        contents are IMPORTED into a running server's schemas by the install
        (`sqlplan`), and they carry migrations that the pinned core was gated
        against. Moving one of them to upstream's tip would hand a server that
        was not rebuilt a set of SQL updates written for a core it is not
        running, and -- unlike the compile, which fails loudly -- the failure
        shape there is a world that boots and is quietly wrong. The approved
        design says so in four words ("`*-db` sources stay at their pin"), and
        this is where those four words live.

        Matched on the repository NAME rather than on a per-source catalog flag,
        which is a deliberate and stated cost. A flag would be exact; it would
        also be a fifth thing every future entry has to remember to set, whose
        default (move it) is the dangerous one. The suffix is upstream CMaNGOS's
        own convention for every one of these repositories (`tbc-db`,
        `classic-db`, `wotlk-db`, `cata-db`), and `test_catalog` pins the
        derivation against every shipped entry so a source this rule reads
        wrongly is a failing test rather than a live surprise.
        """
        return tuple(
            source for source in self.entry.emulator.sources if not held_at_its_pin(source)
        )

    def app_written_paths(self, server_dir: Path) -> Mapping[str, tuple[str, ...]]:
        """Paths inside a source's checkout that THIS APP wrote, per source `dest`.

        What `local_edits()` is told to ignore, because a path this app
        overwrites is a path a `reset --hard` restores to upstream's version and
        a press then puts back: the spine's compose files by
        `_rewrite_what_we_own()`, a family's carried patches by
        `apply_carried_patches()`. Which of the two a path needs is not in this
        mapping, so neither put-back reads it (T173).

        **The spine's own contribution is the compose files, and it was found by
        a test rather than reasoned about.** AzerothCore's core source has
        `dest: "."` -- the server directory IS the checkout -- and that
        repository tracks its own `docker-compose.yml`, which
        `stage_generate_compose()` then overwrites with this app's marked one
        (its docstring calls that "the one recognised exception"). So every
        healthy WotLK install has a modified tracked file in its checkout, and
        the first version of this route did both wrong things with it at once:
        `local_edits()` reported it, so the update was refused on every WotLK
        install there has ever been; and with the guard silenced the reset put
        upstream's compose back, after which `rebuild()`'s own guard refused
        with "these compose files were not written by Yu'lon" and the press
        ended having broken the install it was updating.

        CMaNGOS adds the paths its carried patches edit. Its sources all live
        under `src/`, so it never meets the compose case, and the two
        contributions have never overlapped -- which is why this is a mapping
        per `dest` rather than one list.
        """
        return {".": composegen.COMPOSE_FILES} if self._checkout_is_the_server_dir() else {}

    def _checkout_is_the_server_dir(self) -> bool:
        """Is a source cloned into the server folder itself, with this app's compose over it?

        AzerothCore's core (`dest: "."`) is; no CMaNGOS source is. The spine's
        half of `app_written_paths()`, and T170's test for whether the folder
        can hold the repository's own `docker-compose.yml` at all, and T173's
        for whether an update writes the compose files back -- asked on its own
        because a CMaNGOS family adds its patch paths to that mapping.
        """
        return COMPOSE_STAGE in self.stage_names() and any(
            source.dest == "." for source in self.entry.emulator.sources
        )

    def _rewrite_what_we_own(
        self, server_dir: Path, opts: InstallOptions, state: InstallState
    ) -> Iterator[str]:
        """Put this app's own files back into a checkout the fetch just reset. Spine: compose.

        `rebuild_stages()` deliberately excludes `generate-compose` because it
        "rewrites files a running server is using", and that exclusion is right
        for a rebuild, which re-clones nothing. It is exactly wrong here: the
        reset has ALREADY replaced this install's compose file with upstream's,
        so the choice is not "rewrite it or leave it" but "rewrite it or hand
        `rebuild()` a folder it refuses". Writing it back is restoring what was
        there, not changing it.

        Nothing is written for a family whose sources all live in
        subdirectories -- the three CMaNGOS entries -- because a reset inside
        `src/mangos-tbc` cannot touch a file at the server dir. The condition is
        `_checkout_is_the_server_dir()`, the fact the spine's half of
        `app_written_paths()` is read off, and not that mapping itself: the
        CMaNGOS family adds its carried patch's paths to it, so on TBC and
        Vanilla it was never empty, and both directions of the press rendered
        all three compose files over an install the fetch had not touched
        (T173). That threw away a player's own line in the override, and gave a
        TBC or Vanilla install made before T169 its `./logs` bind without the
        `LogsDir` that T169's Repair sets with it -- the Repair then read the
        file as current and was never offered again. Tortoise carries no patch
        and was never rewritten.
        """
        if not self._checkout_is_the_server_dir():
            return
        yield from self.stage_generate_compose(
            StageContext(
                server_dir=server_dir,
                client_dir=opts.client_dir,
                state=state,
                # None, and not the press's own event: this write is the
                # RECOVERY of a file the fetch already replaced, and a Stop
                # landing inside it would leave the install with upstream's
                # compose and no route that puts it back.
                cancel=None,
                secrets=self.resolve_secrets(server_dir),
            )
        )

    def check_carried_patches(self, server_dir: Path) -> Iterator[str]:
        """Resolve every carried patch against the moved sources WITHOUT writing. Spine: none.

        The gate the approved design puts in front of the compile, and the
        reason it is a dry run rather than the real apply: the answer is needed
        while putting the sources back is still free. A `PatchError` here means
        upstream has moved under a patch this project carries, and the only
        outcomes then available are "build without the patch" -- which is the
        one thing the design forbids in as many words, because the patch is
        there to stop a measured defect -- and "put everything back". This
        method is what makes the second one possible.
        """
        return iter(())

    def apply_carried_patches(self, server_dir: Path) -> Iterator[str]:
        """Write every carried patch into the moved sources. Spine: none.

        **Not a duplicate of the dry run, and not optional.** `rebuild_stages()`
        deliberately excludes `patch-sources` -- a rebuild does not re-clone, so
        there was never anything to re-patch -- and this route DOES re-clone, by
        `reset --hard` onto upstream's tip, which discards the patch the install
        wrote. Handing `rebuild()` a tree in that state would compile the
        unpatched source: exactly the outcome the dry run above was run to
        prevent, arriving one step later. Measured as a reading of the code
        rather than of a box: `patch.apply()` writes into `contrib/
        vmap_extractor/...` inside the core checkout, and `git.RunnerGit._update()`
        is `fetch` + `reset --hard FETCH_HEAD`.

        It runs AFTER the dry run and not instead of it, because a dry run that
        has already passed makes this one a formality that cannot refuse -- and
        if it somehow does, the sources are put back by the same handler.
        """
        return iter(())

    def update_to_latest(
        self,
        options: InstallOptions | None = None,
        *,
        to_pin: bool = False,
        cancel: threading.Event | None = None,
        rewritten_ok: Collection[str] = (),
    ) -> Iterator[str]:
        """Move this install's sources and rebuild on them. Yields output live (T64).

        `rewritten_ok` names the sources whose rewritten history the player's
        confirmation already described (T126); see `_release_targets()`.

        The owner's ask of 2026-09-15, in one route for all four families: "a
        update server to latest, with a warning and recommendation to take a
        backup before updating". The warning and the backup are the view's
        (`ui/controller_view.py`) and the copy is `installer.update_to_latest_
        confirmation()`'s; what is here is the move, its refusals, and putting
        everything back when the thing it was moved for does not work.

        `to_pin` aims the same route at `EmulatorSource.rev` instead of at
        upstream's tip -- the way BACK, and the design's reason for it being the
        same route rather than a second one is that every refusal, every restore
        and every record above is the same sentence in both directions. A
        separate "return" path would be a second set of guards to keep in step
        with these, and the one it would be easiest to forget is the one that
        matters most: a return that did not check for local commits would
        discard work somebody did on top of an untested build.

        **The order is the whole design, and each step is where it is because of
        what is still free at that point.**

        1. `_refuse_unless_rebuildable()` first, because a folder this app does
           not own, or one with no install record, is a folder nothing here may
           fetch into. It is the rebuild's own guard, reused: this press ENDS in
           a rebuild, so a press that would be refused there must be refused
           before it moves anything rather than after.
        2. The three source refusals (`_refuse_unless_updatable()`), all of them
           for every source, BEFORE the first fetch. A route that moved source
           one and then refused source two would leave a tree half a version
           ahead of the image, which is the state this whole method is arranged
           to prevent.
        3. The moves, one source at a time, each remembered as it happens, then
           the family's reading of what they brought (`check_moved_sources()`,
           T179), which can refuse.
        4. The carried patches: resolved dry (which can refuse), then written;
           then the family's background work stopped (`before_rebuild()`).
        5. `rebuild()`, unchanged and in full, rollback tags included. This is
           deliberately NOT a copy of the rebuild with sources bolted on: the
           image rollback, the recipe restore and the "nothing was touched"
           promise are that method's, they are hard-won, and a second
           implementation of them would be a second one to keep correct. What
           the family does with the servers down rides into it
           (`servers_down_work()`, T179: TrinityCore's changed world tables,
           before the new build starts); its rollback half puts the sources back
           first, so the old build's tables come from the old checkout.
        6. The family's after-work on the running build (`after_update()`, T179:
           what was left as it was, the map data's sentence), before the closing
           line.

        Every failure from step 3 through step 5 puts every moved source back on
        the commit it came from, so what is on disk and what the running image was
        compiled from agree (a step-6 failure leaves them: they already agree).
        The step-5 exceptions are a build the rebuild KEPT
        (`WorldStoppedAfterReadyError`, T71) and a rollback that stopped before the
        old build was back on its tags (`RollbackNotDone`, T197): either way the
        new build is what the tags name, so its sources stay with it and are
        recorded, for the same invariant (`SOURCES_KEPT_NOTE`, T179; `SOURCES_LEFT_NOTE`).
        A rollback that left the tags MIXED has no new build to keep them with: they
        go back, nothing is recorded, and the sentence says a Rebuild is owed
        (`SOURCES_MIXED_NOTE`, fix round 1).
        That is the one invariant a user cannot check for themselves and the one
        that quietly breaks everything afterwards: a Modules tab reading a source
        tree that is a hundred commits ahead of the binary answering on the port
        is a tab telling the truth about the wrong thing.

        Raises:
            InstallerError: any refusal, any stage that failed, or a cancel. The
                message is the sentence a user reads, and when sources had
                already moved it says that they were put back.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        state = self._refuse_unless_rebuildable(server_dir)
        # T217 (Decision 9): asked here, before the first fetch, because `rebuild()`
        # now always receives this route's work and the work finishes only what the
        # family's own work finishes. Mixed image tags are repaired by a Rebuild
        # alone (`START_REFUSED_FILE`), never by moving the sources under them.
        owed = owed_start_refusal(server_dir)
        if owed is not None:
            raise InstallerError(f"{owed} Nothing was started.")
        moving = self.sources_that_move()
        if not moving:
            raise InstallerError(
                f"{self.entry.name} has no source this app may update: every repository it "
                "installs is a database repository, which stays on the commit its server was "
                "tested against. Nothing was started."
            )
        if to_pin and any(source.rev is None for source in moving):
            raise InstallerError(
                f"{self.entry.name} does not pin every source it builds from, so there is no "
                "tested commit to return to. Nothing was started."
            )
        plan = self._refuse_unless_updatable(server_dir, moving)
        # T124: the count on the Server tab was taken against the HEADs this
        # press is about to move. Dropped here, before the first fetch, so a
        # count asked while the press runs is asked again rather than served
        # the pre-press figure; and dropped again in the `finally` below, for
        # a count cached while the press ran.
        upstream.forget(server_dir)
        # T126: where a source follows its releases, "latest" is the newest
        # release's commit. Resolved for every such source BEFORE the first
        # fetch, for `_refuse_unless_updatable()`'s reason: a press that moved
        # the core and then could not say which release the bots are on would
        # leave the folder half a version ahead of the image.
        targets = {} if to_pin else self._release_targets(plan, rewritten_ok)
        where = "the commit this app was tested against" if to_pin else "the newest upstream code"
        press = (
            server_build_presses.RETURN_TO_PIN if to_pin else server_build_presses.UPDATE_TO_LATEST
        )
        yield f"Moving {self.entry.name}'s sources in {server_dir} to {where}."
        yield RETURN_TO_PIN_OPENING_NOTE if to_pin else UPDATE_TO_LATEST_OPENING_NOTE
        if self._snapshot is not None and self.snapshot_databases():
            yield copy_opening_line(self.snapshot_databases())
        for said in targets.values():
            yield said.line
        self._check_cancel(cancel)
        route = "the return to the tested commit" if to_pin else "the update to the newest code"
        try:
            moved: list[tuple[EmulatorSource, Path, str]] = []
            try:
                for source, dest, old in plan:
                    self._check_cancel(cancel)
                    yield f"Fetching {source.repo} into {source.dest}."
                    # `rev=None` is what makes this an update rather than a re-pin:
                    # `git.RunnerGit.clone()` sees an existing `.git`, runs
                    # `_update()` -- fetch, then `reset --hard FETCH_HEAD` -- and
                    # `_pin()` then returns immediately. With `rev=source.rev` the
                    # same two calls run, the reset is skipped and the pin is the
                    # one move (T166), which is the way back. One seam, two
                    # directions, no second fetch path.
                    #
                    # APPENDED BEFORE the call and not after: `clone_lines()` can
                    # fail half way through, after the reset has already landed, and
                    # a source that is not in `moved` is a source nothing puts back.
                    moved.append((source, dest, old))
                    yield from self._clone_lines(
                        git.CloneSpec(
                            url=source.url,
                            dest=dest,
                            branch=source.branch,
                            sparse_path=source.sparse_path,
                            depth=source.depth,
                            rev=source.rev if to_pin else self._latest_rev(source, targets),
                        ),
                        UPDATE_SOURCES_STAGE,
                    )
                    yield self._moved_line(source, dest, old)
                self._check_cancel(cancel)
                # T179: the family reads what the move brought (TrinityCore's SQL
                # snapshot) and may refuse it, while every source can still go back
                # and nothing has been built, written or stopped.
                changes = yield from self.check_moved_sources(server_dir, moved, to_pin=to_pin)
                yield from self.check_carried_patches(server_dir)
                yield from self._rewrite_what_we_own(server_dir, opts, state)
                yield from self.apply_carried_patches(server_dir)
                # After every refusal -- the source checks above included -- and
                # before the compile: `rebuild()`'s reason, one step earlier. Not
                # before the first fetch (as until T179 Task 6): what this stops
                # (the movement-map job) reads no checkout, and a press the source
                # checks refuse must leave it running. A stop that fails restores.
                yield from self.before_rebuild(server_dir, route, press)
            except (InstallerError, OSError) as exc:
                if isinstance(exc, UpdateRefused) and not to_pin:
                    # T179: the same commit is not offered again (`upstream_news`).
                    self._remember_refused(server_dir, exc)
                # `OSError` as well, and not for symmetry: everything between the
                # first fetch and the compile WRITES -- `_rewrite_what_we_own()`
                # renders WotLK's three compose files, `apply_carried_patches()` writes into
                # the checkout -- and a full disk or a read-only mount surfaces as a
                # bare `OSError` that no `InstallerError` wraps. Skipping the
                # restore on it would leave the folder ahead of the image for the
                # one failure most likely to happen twice in a row (cold review
                # round 2, 2026-09-16).
                failed = yield from self._restore_the_folder(moved, server_dir, opts, state, press)
                raise InstallerError(f"{exc} {_sources_note(failed)}") from exc
            # T179: what the family does while the rebuild's servers are down. Its
            # rollback half needs the OLD checkout, so the sources go back first,
            # inside the rollback, and the handler below does not do it twice.
            #
            # T217: and for EVERY family now, not only one with work of its own. The
            # rollback puts the tags back, then the sources (the old WotLK worldserver
            # reads its module's SQL from the folder), then the copy of the databases
            # the new build could change, and only then starts the old build. The
            # copy is taken with the servers down, right before the new build first
            # starts.
            sources_back = False
            sources_failed: list[tuple[EmulatorSource, Path, str, str]] = []
            copy = _UpdateCopy(
                names=self.snapshot_databases() if self._snapshot is not None else (),
                not_copied=self.snapshot_left_out(),
            )
            family = self.servers_down_work(server_dir, changes, press=press)

            def forward(stage_ctx: StageContext) -> Iterator[str]:
                if copy.names:
                    yield from self._take_copy(server_dir, copy)
                if family is not None:
                    yield from family.forward(stage_ctx)

            def back(stage_ctx: StageContext) -> Iterator[str]:
                nonlocal sources_back
                failed = yield from self._restore_the_folder(moved, server_dir, opts, state, press)
                sources_back = True
                sources_failed.extend(failed)
                # The copy goes back even when a folder did not: with the servers
                # stopped it is harmless, and it leaves one fix to make, not two.
                copy_problem = ""
                try:
                    yield from self._put_copy_back(server_dir, copy)
                except LeaveStopped as exc:
                    copy_problem = str(exc)
                if failed:
                    # T217 (B3): the old build would read the new module's SQL from
                    # the folder that did not go back. It is not started.
                    said = [source_not_back(s.repo, dest, old, why) for s, dest, old, why in failed]
                    raise LeaveStopped(" ".join([*said, copy_problem]).strip())
                if copy_problem:
                    raise LeaveStopped(copy_problem)
                if family is not None:
                    yield from family.back(stage_ctx)

            work = ServersDownWork(
                prepare=family.prepare if family is not None else lambda: iter(()),
                forward=forward,
                back=back,
                settle=family.settle if family is not None else lambda: None,
                keep=family.keep if family is not None else lambda: iter(()),
                done=family.done if family is not None else lambda: iter(()),
                database=lambda: self._copy_database_sentence(copy),
                came_back=lambda: self._forget_older_copies(server_dir, copy),
                did_not_come_back=lambda: self._old_build_down(copy),
                finishes_start_refusal=family is not None and family.finishes_start_refusal,
            )
            try:
                yield from self.rebuild(opts, cancel=cancel, servers_down=work, press=press)
                yield from work.done()
                self._forget_older_copies(server_dir, copy)
            except WorldStoppedAfterReadyError as exc:
                # T71: the rebuild KEPT the new build -- it came up, then stopped
                # on its data -- so the sources it was made from stay with it, and
                # are recorded as what the running build is (T179 fix round 2).
                # Putting the old commits back here would be this route's own
                # invariant broken by its own recovery.
                yield from work.done()
                # T217: the database stays with the build that runs, as the sources do.
                self._forget_older_copies(server_dir, copy)
                self._record_source_revs(
                    server_dir,
                    state,
                    moved,
                    {repo: said.tag for repo, said in targets.items() if said.tag},
                )
                # Guarded: the kept build is the sentence this press ends on, and
                # a failure of the after-work is added to it, never in its place.
                also = self._copy_kept(copy)
                try:
                    yield from self.after_update(server_dir, changes, press=press, cancel=cancel)
                except InstallerError as after:
                    also = f" {after}"
                raise carry_detail(
                    exc,
                    WorldStoppedAfterReadyError(
                        f"{exc} {SOURCES_KEPT_NOTE}{also}", sources_kept=True
                    ),
                ) from exc
            except RollbackNotDone as exc:
                if exc.mixed:
                    # Fix round 1: the tags name neither build, so there is no new
                    # build to keep the sources with or to record. They go back to
                    # the commits the record still names, and the sentence says the
                    # server needs a Rebuild before it can start.
                    work.settle()
                    failed = yield from self._restore_the_folder(
                        moved, server_dir, opts, state, press
                    )
                    # T217: the sources went back, so the database goes back with them
                    # (servers stopped; every start stays refused until a Rebuild).
                    database = ""
                    try:
                        yield from self._put_copy_back(server_dir, copy)
                    except LeaveStopped as not_back:
                        database = f" {not_back}"
                    if copy.put is not None:
                        database = f" {copy_back_with_the_sources(copy.put)}"
                    sources = (
                        mixed_note(exc.touched)
                        if not failed
                        else f"{SOURCES_NOT_ALL_BACK_NOTE} {SOURCES_MIXED_REBUILD}"
                    )
                    raise carry_detail(
                        exc,
                        RollbackNotDone(
                            f"{exc} {sources}{database}",
                            touched=exc.touched,
                            mixed=True,
                        ),
                    ) from exc
                # T197: the rollback stopped before the old build was back on its
                # tags, which still name the NEW build, and a start runs them. Its
                # sources stay with it and are recorded, as for the kept build above;
                # putting the old commits back would leave them under a build they
                # did not make, with a sentence saying the two agree again.
                also = ""
                try:
                    yield from work.keep()
                except (InstallerError, OSError) as kept_failed:
                    also = f" {kept_failed}"
                if (
                    owed_start_refusal(server_dir) == UNTESTED_BUILD_REFUSAL
                    and self.family_start_refusal(server_dir) is not None
                ):
                    # T223 (lead ruling, option 1): the family's own record refuses
                    # Start, and T179's "Finish the world update" imports the kept
                    # build's tables and then starts it -- the owner-approved exit.
                    # Asked of the record `keep()` left, not of a flag: map data
                    # alone, or a record that could not be written, refuses nothing.
                    left_over = forget_owed_start(server_dir)
                    if left_over:
                        also += f" {left_over}"
                # T217: the database stays with the new build too; its copy is kept, and
                # so are the older ones -- no build reported ready (live proof, item 5).
                also += self._copy_kept(copy)
                self._record_source_revs(
                    server_dir,
                    state,
                    moved,
                    {repo: said.tag for repo, said in targets.items() if said.tag},
                )
                try:
                    yield from self.after_update(server_dir, changes, press=press, cancel=cancel)
                except InstallerError as after:
                    also = f"{also} {after}"
                left = (
                    SOURCES_LEFT_NOTE
                    if exc.touched
                    else untouched_note(self.start_refusal(server_dir))
                )
                raise carry_detail(
                    exc,
                    RollbackNotDone(f"{exc}{also} {left}", touched=exc.touched, sources_kept=True),
                ) from exc
            except InstallerError as exc:
                # AFTER `rebuild()` has done its own rollback, never instead of it.
                # It puts the IMAGE back; this puts the SOURCE back; and it is the
                # pair that makes the folder and the running container agree again.
                work.settle()
                if not sources_back:
                    sources_failed.extend(
                        (yield from self._restore_the_folder(moved, server_dir, opts, state, press))
                    )
                if isinstance(exc, OldBuildNotStopped):
                    # T217: the old build may still be restarting; no "stopped", and
                    # no "agree again" about a server that does not run.
                    raise carry_detail(
                        exc, OldBuildNotStopped(f"{exc} {SOURCES_PUT_BACK_NOT_STOPPED_NOTE}")
                    ) from exc
                if isinstance(exc, ServersLeftStopped):
                    # T179: nothing runs, so the note must not say it does. T217: and
                    # when it is a folder or the database that did not go back, the
                    # note says that instead.
                    if sources_failed:
                        note = SOURCES_NOT_ALL_BACK_NOTE
                    elif copy.put_failed or copy.old_build_down:
                        note = SOURCES_PUT_BACK_DATABASE_NOT_NOTE
                    else:
                        note = SOURCES_PUT_BACK_STOPPED_NOTE
                    raise carry_detail(exc, ServersLeftStopped(f"{exc} {note}")) from exc
                if isinstance(exc, RebuildChangedTheServer):
                    # T228: the rebuild's sentence is true after a Stop, and so is
                    # this one; the type carries that through. T217: never "agree
                    # again" after a folder that did not go back.
                    if sources_failed:
                        note = SOURCES_NOT_ALL_BACK_NOTE
                    else:
                        note = SOURCES_PUT_BACK_NOTE if exc.up else SOURCES_PUT_BACK_NOT_UP_NOTE
                    # T247 live review: a clean put-back stays one only if the
                    # sources went back too.
                    kind = (
                        StoppedAndPutBack
                        if isinstance(exc, StoppedAndPutBack) and not sources_failed
                        else RebuildChangedTheServer
                    )
                    raise carry_detail(exc, kind(f"{exc} {note}", up=exc.up)) from exc
                raise carry_detail(
                    exc, InstallerError(f"{exc} {_sources_note(sources_failed)}")
                ) from exc
            except BaseException:
                # Not a refusal: a bug, an interrupt, a reader that went away. The
                # record of tables to import is put back if nothing was imported
                # (fix round 3); nothing may be yielded here (`rebuild()`'s reason).
                # T217: nor is the copy put back -- that needs a yield and a restore.
                work.settle()
                raise
            self._record_source_revs(
                server_dir,
                state,
                moved,
                {repo: said.tag for repo, said in targets.items() if said.tag},
            )
            landed = (
                "the commit this app was tested against" if to_pin else "the newest upstream code"
            )
            yield from self.after_update(server_dir, changes, press=press, cancel=cancel)
            yield f"{self.entry.name} is running on {landed}."
        finally:
            # T124's second drop, and the one that covers EVERY way out: a
            # success, a failure after the sources went back, a Cancel, and a
            # generator closed half way (GeneratorExit is none of the handled
            # exceptions). It exists for a count asked WHILE the press ran,
            # which cached the moved -- or about-to-be-restored -- HEADs.
            upstream.forget(server_dir)

    def _release_targets(
        self,
        plan: Sequence[tuple[EmulatorSource, Path, str]],
        rewritten_ok: Collection[str] = (),
    ) -> dict[str, ReleaseTarget]:
        """The commit "latest" means for each source that follows its releases (T126).

        Asked of GitHub's releases API, per source, now: `upstream.newest_release()`
        holds why a tag is resolved at the moment of asking rather than by name.
        A source whose checkout already CONTAINS that release -- a return to a
        pin newer than the newest release, say -- stays where it is: moving it
        onto the release would be a step backwards dressed as an update.

        Raises `InstallerError` when GitHub cannot say, because the other answer
        -- the branch tip -- is exactly the in-between commit this source was
        marked to avoid.

        **A DIVERGED release -- upstream rewrote its history -- moves, but only
        if the question the player answered said so** (`rewritten_ok`, the repos
        whose `rewritten_line()` the confirmation carried). The lead's ruling
        (Codex's final pass): refusing would lock an install built on the
        rewritten-away history (TortoiseBots' old pin `fd7ec9ec`, T120) out of
        updates for good, and moving silently would drop commits the server was
        built from without a word. Not acknowledged, it raises `RewrittenHistory`
        before anything moves, and the route asks again with the line in it.
        """
        found: dict[str, ReleaseTarget] = {}
        for source, dest, old in plan:
            if source.follow != "releases":
                continue
            slug = upstream.github_slug(source.repo)
            release = (
                None
                if slug is None
                else upstream.newest_release(slug, get=self._seams.upstream_get)
            )
            if slug is None or release is None:
                raise InstallerError(
                    f"{source.repo} follows its published releases, and Yu'lon could not ask "
                    f"GitHub which release is the newest. Nothing in that folder was changed; "
                    f"try again when GitHub can be reached."
                )
            stands = upstream.compare(slug, old, release.sha, get=self._seams.upstream_get)
            if stands is None:
                # Refused, not guessed (T126 review, Codex high): without the
                # comparison nobody knows whether the release is ahead of this
                # checkout or behind it, and moving onto it could be a step back.
                raise InstallerError(
                    f"{source.repo} follows its published releases, and GitHub did not answer "
                    f"whether {release.tag} is ahead of what {dest} is on. Nothing in that "
                    f"folder was changed; try again later."
                )
            if stands.ahead == 0 and old != release.sha:
                # No tag recorded: the checkout is PAST the release, and a
                # version line naming the release would name a commit it is not on.
                past = _commits(stands.behind)
                found[source.repo] = ReleaseTarget(
                    tag="",
                    rev=old,
                    line=(
                        f"{source.repo} follows its releases; the newest, {release.tag} "
                        f"({release.sha[:7]}), is already in {old[:7]}, which is {past} past "
                        f"it, so it stays there."
                    ),
                )
                continue
            said = (
                f"{source.repo} follows its releases; the newest is {release.tag} "
                f"({release.sha[:7]})."
            )
            if stands.diverged:
                warning = rewritten_line(source.repo, release.tag, stands.behind)
                if source.repo not in rewritten_ok:
                    raise RewrittenHistory(source.repo, warning)
                said = f"{said}\n{warning}"
            found[source.repo] = ReleaseTarget(tag=release.tag, rev=release.sha, line=said)
        return found

    @staticmethod
    def _latest_rev(source: EmulatorSource, targets: Mapping[str, ReleaseTarget]) -> str | None:
        """What an update checks out for `source`: its release's commit, or `None` for the tip."""
        said = targets.get(source.repo)
        return said.rev if said is not None else None

    def _refuse_unless_updatable(
        self, server_dir: Path, moving: Sequence[EmulatorSource]
    ) -> tuple[tuple[EmulatorSource, Path, str], ...]:
        """The three source refusals, for every source, before anything is fetched.

        Each is the prior art's own gate, read out of `dml wow update`
        (`cli/dml` 11163-11250) and `wow-manage.sh`'s `update_server_source`
        (7319-7460) on 2026-09-16, and each is FAIL-CLOSED where those two were
        not:

        * **origin.** Both prior launchers refuse a checkout whose `origin` is
          not the fork the server needs -- wow-manage by asking "pull from this
          remote anyway?", `dml` by making it a hard error with no override,
          because "pulling upstream AzerothCore here would break the playerbots
          integration". `dml`'s reading is taken. The question wow-manage asks
          is one nobody has the information to answer at the moment it is asked.
        * **a dirty tree.** Both prior launchers keep going: they stash the
          edits, pull, and pop them back on top, and wow-manage's own comment
          admits what happens when that conflicts ("the updated file wins").
          This refuses instead, and the reason is that a stash is a promise this
          app cannot keep -- `_update()` is `reset --hard`, not `pull --ff-only`,
          and the pop lands on a tree that may share no history with the one the
          edit was made against. A refusal that names the files is a worse
          evening and a better outcome than a silent three-way merge into
          somebody's server source. `app_written_paths()` is subtracted first;
          see `git.RunnerGit.local_edits()`.
        * **local commits.** NEITHER prior launcher checks. `git pull --ff-only`
          would have refused a diverged branch, which is a third of the case;
          this route resets, which would discard the commits outright and leave
          them reachable only through the reflog. `git.HistoryReader`'s own
          docstring is the argument: a checkout somebody committed their work
          into is perfectly clean by `status`. **This one depends on
          `git._only_grafts()` to stay usable at all** (T82, measured live
          2026-09-16): a `depth: 1` source that has been updated and then
          returned to its pin carries two grafted, unconnected commits in
          `.git/shallow`, counts as one commit ahead of upstream, and without
          that second layer both buttons refuse it for ever.

        `None` from any of the three reads refuses, and the sentence says which
        of the two it is. "We could not ask" is never "there is nothing to
        lose" -- the rule every `None` in `git.py` is documented under.

        **The closing clause changes half way down the list, and the change is
        the point.** The refusals above `no_local_commits` end "nothing was
        fetched and nothing was changed", and that is literally true. The ones
        below it end "nothing in that folder was changed", because
        `no_local_commits()` FETCHES -- its own docstring says so, and names the
        network round trip it pays -- so by then objects and `FETCH_HEAD` have
        been written into `.git`. Nothing in the working tree has moved, which
        is what the user is being told and what they can check; saying "nothing
        was fetched" there would be a sentence this method knows to be false
        (cold review round 2, 2026-09-16).

        Returns `(source, dest, sha)` per source, with the sha read HERE rather
        than inside the loop that moves them: the restore path needs a commit
        that was read before anything fetched, and a read taken after the first
        source has already moved is a read of a tree this press has changed.
        """
        ours = self.app_written_paths(server_dir)
        plan: list[tuple[EmulatorSource, Path, str]] = []
        for source in moving:
            dest = server_dir / source.dest
            existing = self._remote_of(dest)
            if existing is None:
                raise InstallerError(
                    f"{dest} is not a checkout of {source.url} that Yu'lon can read -- git would "
                    f"not say what it is a checkout of. Nothing was fetched and nothing was "
                    f"changed."
                )
            if not git.same_repo(existing, source.url):
                raise InstallerError(
                    f"{dest} is a checkout of {existing}, not of {source.url}. Updating it would "
                    f"fetch somebody else's code into your server, so nothing was fetched and "
                    f"nothing was changed."
                )
            edits = self._seams.local_edits(dest, ours.get(source.dest, ()))
            if edits is None:
                raise InstallerError(
                    f"Yu'lon could not ask git whether {dest} has changes of your own in it, and "
                    f"an update replaces that checkout's files. Nothing was fetched and nothing "
                    f"was changed."
                )
            if edits:
                raise InstallerError(
                    f"{dest} has changes of your own in it ({_named(edits)}). An update replaces "
                    f"that checkout's files with upstream's, which would throw them away, so "
                    f"nothing was fetched and nothing was changed. Copy them somewhere safe and "
                    f"put the files back as git has them, then press this again."
                )
            unmoved = self._seams.no_local_commits(dest, source.branch)
            if unmoved is None:
                raise InstallerError(
                    f"Yu'lon could not ask git whether {dest} carries commits of its own -- the "
                    f"fetch that question needs did not answer, so upstream could not be reached "
                    f"either. Nothing was fetched and nothing was changed."
                )
            if not unmoved:
                raise InstallerError(
                    f"{dest} carries commits that upstream does not, and an update moves it onto "
                    f"upstream's newest commit, which would leave them reachable only through "
                    f"git's reflog. Nothing in that folder was changed."
                )
            sha = self._seams.head_sha(dest)
            if sha is None:
                raise InstallerError(
                    f"Yu'lon could not read which commit {dest} is on, so it could not promise to "
                    f"put that checkout back if the new build failed. Nothing in that folder "
                    f"was changed."
                )
            plan.append((source, dest, sha))
        return tuple(plan)

    def _moved_line(self, source: EmulatorSource, dest: Path, old: str) -> str:
        """What one source's move is reported as: the two shas, or that nothing moved."""
        new = self._seams.head_sha(dest)
        if new is None:
            return f"{source.repo} was updated; git would not say what it is on now."
        if new == old:
            return f"{source.repo} was already on {new[:7]}; nothing moved."
        return f"{source.repo}: {old[:7]} -> {new[:7]}."

    def _restore_the_folder(
        self,
        moved: Sequence[tuple[EmulatorSource, Path, str]],
        server_dir: Path,
        opts: InstallOptions,
        state: InstallState,
        press: str,
    ) -> Generator[str, None, list[tuple[EmulatorSource, Path, str, str]]]:
        """Put the sources back AND write this app's own files into them again.

        `press` is the label of the press being put back (T163): the carried
        patch's sentence sends the player to it again.

        **Two halves, and the second is not tidying.** `restore_rev()` is a
        `checkout --force`: it puts the checkout on the old commit and, with it,
        puts UPSTREAM's copy of every tracked file back -- including the ones
        this app overwrote. On AzerothCore that is `docker-compose.yml`, so a
        restore that stopped at the first half would leave the folder on the
        right commit with a compose file this app does not recognise, and the
        NEXT press of anything -- Rebuild included -- would refuse it with "these
        compose files were not written by Yu'lon". The press would have fixed
        the sha and broken the install, which is the same shape as the defect
        `app_written_paths()` exists for, arriving on the recovery path.

        Never raises, for `_put_sources_back()`'s reason: this runs on a path
        that is already failing. A second half that could not run is reported
        rather than thrown, because the sentence in front of it is the one that
        says what actually went wrong.

        Returns the sources that would not go back (T217), and remembers them in
        `SOURCES_OFF_FILE` so every start is refused until they are back.
        """
        failed = yield from self._put_sources_back(moved)
        if failed:
            warned = remember_sources_off(
                server_dir, [(source.repo, dest, old) for source, dest, old, _why in failed]
            )
            if warned:
                yield warned
        if not moved:
            return failed
        # T163: each half names the press that mends IT, and neither is
        # Rebuild. Upstream's compose file in the folder is one Rebuild refuses
        # (`_refuse_unless_rebuildable()`) and so does this same press, which
        # starts with that guard. Repair server files writes over it since
        # T170 (`compose_back_advice()`), and only because the file is still
        # git's own: `composegen.write_plan()` replaces a compose file whole, so
        # a disk that fills part-way leaves the old file untouched. Until T163
        # it truncated first, and a full disk left a cut-off file that is
        # neither git's nor Yu'lon's and that the Repair refuses as well. An
        # unpatched tree is one Rebuild compiles as it stands -- the defect the
        # patch is carried for -- and the install refuses (its build would be
        # skipped); this same press writes the patch before it compiles. Neither
        # sentence says "nothing was compiled": this also runs after a compile
        # that `rebuild()` rolled back.
        try:
            yield from self._rewrite_what_we_own(server_dir, opts, state)
        except (InstallerError, OSError) as exc:
            logger.warning(f"could not put this app's own files back into {server_dir}: {exc}")
            # Only a checkout-is-the-server-dir entry (WotLK) gets here: the
            # rewrite writes nothing anywhere else (T173), so the CMaNGOS
            # sentence T170's cold review added for this branch had no way in.
            yield (
                f"The source folders are back on their old commits, but Yu'lon's own compose "
                f"files could not be written into them again ({exc}), so "
                f"{composegen.BASE_FILE} is the repository's own. "
                f"{compose_back_advice(server_dir)}"
            )
            return failed
        try:
            yield from self.apply_carried_patches(server_dir)
            # T562: the scripts follow the sources back; laid here, with the servers
            # down on a rollback, and a no-op before the compile (nothing was laid yet).
            yield from self.lay_scripts(server_dir, quiet=False)
        except SelfExplainedError as exc:
            # T563: a stage that already said what failed and what to do (a Lua
            # link, a record that could not be saved) is passed through as it
            # stands: "press again" does not mend every one of them.
            logger.warning(f"could not put this app's own files back into {server_dir}: {exc}")
            yield (
                f"The source folders are back on their old commits, but this app's own "
                f"files could not be put into them again. {exc}"
            )
        except (InstallerError, OSError) as exc:
            logger.warning(f"could not put this app's own files back into {server_dir}: {exc}")
            yield (
                f"The source folders are back on their old commits, but the source patch "
                f"Yu'lon carries could not be written into them again ({exc}). Once the "
                f"reason is fixed, press {server_build_presses.under_server_build(press)} "
                "again: it writes the patch before it compiles."
            )
        return failed

    def _put_sources_back(
        self, moved: Sequence[tuple[EmulatorSource, Path, str]]
    ) -> Generator[str, None, list[tuple[EmulatorSource, Path, str, str]]]:
        """Return every source this press moved to the commit it was on. Never raises.

        Never raises because it runs on a path that is ALREADY failing, and a
        second exception there would replace the sentence that says what went
        wrong with one about the recovery. What it does instead is say so, per
        source, in a line the user can act on: a checkout that would not go back
        is the one state this route can end in where the folder and the running
        image disagree, and a person who is told which folder and which commit
        can run two words of git themselves.

        Returns the sources that would not go back, with why (T217): every caller
        picks its closing note from that, and never "agree again" after one.
        """
        failed: list[tuple[EmulatorSource, Path, str, str]] = []
        for source, dest, old in reversed(moved):
            try:
                self._seams.restore_rev(dest, old)
            except (git.GitError, OSError) as exc:
                # T248: the command is for whoever reads the log; the line is words.
                logger.warning(
                    f"could not put {dest} back on {old}: {exc}. To do it by hand: "
                    f"git -C {dest} checkout --detach --force {old}"
                )
                yield (
                    f"{source.repo} in {dest} could NOT be put back on {old[:7]} ({exc}). That "
                    "folder is still on the commit this press moved it to, not the commit the "
                    f"build this server has was made from: put it back on commit {old[:7]}, or "
                    "press "
                    f"{server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)}."
                )
                failed.append((source, dest, old, str(exc)))
                continue
            yield f"{source.repo} was put back on {old[:7]}."
        return failed

    def _take_copy(self, server_dir: Path, copy: _UpdateCopy) -> Iterator[str]:
        """Copy the databases the new build can change; servers down, before it starts (T217).

        A copy that cannot be taken raises: `forward()` raising is "the new build
        never started", which the rebuild rolls back, and nothing in the databases
        was changed.
        """
        if self._snapshot is None:
            return
        yield copy_taking_line(copy.names)
        try:
            copy.taken = self._snapshot.take(server_dir, copy.names)
        except (InstallerError, OSError) as exc:
            copy.take_failed = True
            raise InstallerError(copy_not_taken(copy.names, str(exc))) from exc
        yield copy_taken_line(copy.taken)

    def _put_copy_back(self, server_dir: Path, copy: _UpdateCopy) -> Iterator[str]:
        """Put the copy back in the rollback's window: after the sources, before the old build.

        One that will not go back raises `LeaveStopped`: the old build is not
        started on the database the new one changed (the owner's rule, 2026-10-04).
        """
        if copy.taken is None or self._snapshot is None:
            return
        yield copy_putting_back_line(copy.taken)
        try:
            copy.put = self._snapshot.put_back(server_dir, copy.taken)
        except snapshot.CopyNotUsable as exc:
            copy.put_failed = True
            raise LeaveStopped(copy_not_usable(copy.taken, str(exc))) from exc
        except (InstallerError, OSError) as exc:
            copy.put_failed = True
            raise LeaveStopped(copy_not_put_back(copy.taken, str(exc))) from exc
        yield copy_put_back_line(copy.put)
        # NOT forgotten here: the old build has not started yet, and until it reports
        # ready an older copy may be the only one that brings it back (live proof
        # 2026-10-05, item 5). `came_back` forgets them.

    @staticmethod
    def _copy_kept(copy: _UpdateCopy) -> str:
        """ " " + `copy_kept_note()` when a copy was taken and the new build stays; else ""."""
        return f" {copy_kept_note(copy.taken)}" if copy.taken is not None else ""

    def _copy_database_sentence(self, copy: _UpdateCopy) -> str | None:
        """What the rollback says about the database, or None for its own sentence (T217)."""
        if not copy.names:
            return None
        if copy.take_failed:
            return COPY_NOT_TAKEN_DATABASE
        if copy.put is not None:
            return copy_put_back_database(copy.put, not_copied=copy.not_copied)
        return None

    @staticmethod
    def _old_build_down(copy: _UpdateCopy) -> Callable[[str | None], str] | None:
        """`did_not_come_back` for the update route: a sentence-maker once its copy went back."""
        taken, put = copy.taken, copy.put
        if taken is None or put is None:
            return None
        copy.old_build_down = True
        older = snapshot.older_copies(taken.directory, taken.files)

        def say(not_stopped: str | None) -> str:
            return copy_old_build_down(taken, put, older=older, not_stopped=not_stopped)

        return say

    def _forget_older_copies(self, server_dir: Path, copy: _UpdateCopy) -> None:
        """Keep only this press's copy (owner, 2026-10-04), once it is not the only good one."""
        if copy.taken is None or self._snapshot is None:
            return
        try:
            self._snapshot.prune(server_dir, copy.taken)
        except (InstallerError, OSError) as exc:
            logger.warning(f"could not forget the older update copies in {server_dir}: {exc}")

    def _record_source_revs(
        self,
        server_dir: Path,
        state: InstallState,
        moved: Sequence[tuple[EmulatorSource, Path, str]],
        releases: Mapping[str, str] | None = None,
    ) -> None:
        """Write where each moved source ended up into the install record.

        LAST, after the rebuild succeeded, and that is the point of it: this key
        is read by the tab's version line as "what the running server was built
        from", and a record written before the compile would describe a build
        that may never have happened. A press that fails writes nothing here at
        all -- the sources went back, so the record that is already on disk is
        still true.

        Best-effort like every other write through `write_state()`: a record
        that could not be written costs a version line, and taking the whole
        press down at the end of a successful build to report that would be a
        failure about a decoration.

        **The state is RE-READ here and the caller's copy is not used, and that
        is not tidiness.** `rebuild()` has just run, and on success it calls
        `_clear_error()` — which exists because an install record that keeps a
        stale `last_error` tells a user their working server failed (measured
        on m910q, 2026-09-02). The caller's `state` was read BEFORE that, so
        writing `replace(state, ...)` would put the cleared sentence straight
        back: a press that failed, then a press that succeeded, and a server
        that is running the new build with a record saying the update failed
        (cold review, 2026-09-16). A read that will not answer skips the write
        rather than falling back to the stale copy — losing a version line is
        the cheaper of the two.
        """
        found: list[SourceRev] = []
        for source, dest, _old in moved:
            built = self._seams.head_version(dest)
            if built is None:
                logger.warning(f"could not read what {dest} was built from; not recording it")
                continue
            pin = source.rev or ""
            found.append(
                SourceRev(
                    repo=source.repo,
                    built=built,
                    pin=pin,
                    ahead=self._seams.commits_since(dest, pin) if pin else None,
                    release=(releases or {}).get(source.repo, ""),
                )
            )
        if not found:
            return
        fresh = read_state(server_dir, valid=self.stage_names())
        if fresh is None:
            logger.warning(
                f"{server_dir} would not say what it is after the rebuild, so what this update "
                "built from was not recorded; nothing else was changed."
            )
            return
        # Every OTHER source's record survives: a family could gain a source
        # this press does not move, and dropping its row because this press did
        # not visit it would delete a true reading.
        keep = {rev.repo for rev in found}
        merged = tuple(rev for rev in fresh.source_revs if rev.repo not in keep) + tuple(found)
        # A source that landed has no refused commit any more (T179).
        refused = tuple(pair for pair in fresh.refused_updates if pair[0] not in keep)
        write_state(
            server_dir,
            replace(
                fresh,
                source_revs=tuple(sorted(merged, key=_by_repo)),
                refused_updates=refused,
            ),
        )

    def _remember_refused(self, server_dir: Path, refused: UpdateRefused) -> None:
        """Record the upstream commit an update refused, so the tab stops offering it (T179).

        Best-effort, as every write through `write_state()`: a record that could
        not be written costs one more offer of a press that will refuse again.
        """
        fresh = read_state(server_dir, valid=self.stage_names())
        if fresh is None:
            return
        kept = tuple(pair for pair in fresh.refused_updates if pair[0] != refused.repo)
        pairs = tuple(sorted((*kept, (refused.repo, refused.commit))))
        write_state(server_dir, replace(fresh, refused_updates=pairs))

    def _keep_rollback(
        self, ctx: StageContext, refs: Sequence[str], *, missing_ok: bool = False
    ) -> Generator[str, None, tuple[str, ...]]:
        """Give every image the compile will overwrite its `-rollback` name, or say why not.

        One answer and three refusals, all before the compile (owner answer 2:
        ALWAYS keep a rollback), and since T170 a second answer: with
        `missing_ok` -- the Rebuild press, whose confirmation said so -- images
        that are not all there keep NOTHING, say so, and the compile goes on.
        Not all there is not a build: a rollback of three images out of four
        could not bring a server back, so none is kept of a partial set either.

        * the images are all there and all tagged -- the rollback is kept;
        * the images are there and docker will not tag one -- refused, with
          the tags already made taken back, because building anyway would
          overwrite the only copy of the running build with the rollback the
          owner asked for unkept. Docker's words are in the sentence:
          "read-only layer store" is a different evening from "no such image";
        * the images' names are not all there, and this install's containers
          still hold a whole build by image id (a retag moved a name away, an
          `rmi -f` took it off) -- that build is kept, by id, and it is an
          ordinary rebuild with a rollback (`_old_build_by_id()`, T170 round 2);
        * no whole build can be named that way -- refused unless `missing_ok`,
          and the sentence names the press that compiles them: Rebuild, which
          is `missing_ok` (T170);
        * docker will not say whether they are there, or (round 3) what build
          the containers run -- refused. `None` is
          "could not ask", and destructive work on an unanswered question
          fails closed.

        Until the adversarial review of 2026-09-08 the last two went ahead
        with a sentence saying no rollback was kept, which contradicted the
        confirmation the user had just agreed to and made the one press with
        no safety net look exactly like the others.

        Returns the rollback names kept -- empty only when `missing_ok` found
        no whole build to keep.
        """
        present = self._seams.images_built(refs)
        if present is None:
            raise InstallerError(
                "Docker would not say whether this install's images exist, so the build you "
                "have now could not be kept as a rollback and nothing was compiled over it. "
                "Nothing was started. Check the docker daemon is up, then press the same entry "
                # T155: `stage_recreate()`'s reason -- all three entries reach this.
                f"under \u201c{server_build_presses.SERVER_BUILD}\u201d on the Modules tab "
                "again."
            )
        # T170 round 2: names gone is not a build gone. A retag or an `rmi -f`
        # takes the NAME off an image a container still holds by id, and that
        # container is the build the server runs -- so it is kept by its id.
        sources = {ref: ref for ref in refs} if present else self._old_build_by_id(ctx, refs)
        if sources is None and missing_ok:
            logger.info(f"rebuild of {self.entry.id}: no build to keep as a rollback (T170)")
            yield NO_ROLLBACK_KEPT
            return ()
        if sources is None:
            raise InstallerError(
                "This install's images are not all on the daemon under their tags, and no "
                "container of this install still holds a whole build, so there is no build to "
                "keep as a rollback, and this press does not compile without one. Nothing was "
                "compiled. "
                # T170: Rebuild compiles missing images since then (T164 sent
                # the player to remove and install again). T155: the press by
                # its label and its menu. It runs for a WSL server too.
                f"Press {server_build_presses.under_server_build(server_build_presses.REBUILD)} "
                "first: with the images gone it compiles them without a rollback, and its "
                "confirmation says so before it starts. Then press this one again."
            )
        kept: list[str] = []
        for ref in refs:
            back = ref + ROLLBACK_TAG_SUFFIX
            problem = self._seams.tag_image(sources[ref], back)
            if problem:
                self._let_go(kept)
                raise InstallerError(
                    f"The build you have now could not be kept as a rollback ({problem}), so "
                    f"nothing was compiled over it. Nothing was started; the server you have "
                    f"is running exactly as it was."
                )
            kept.append(back)
        by_id = [ref for ref in refs if sources[ref] != ref]
        found = (
            f" {len(by_id)} of them had lost their names, so they were kept by the image their "
            "containers are running."
            if by_id
            else ""
        )
        if by_id:
            logger.info(f"rebuild of {self.entry.id}: rollback kept by image id for {by_id}")
        yield (
            f"Kept the build you have now as a rollback ({len(kept)} images tagged "
            f"{ROLLBACK_TAG_SUFFIX}).{found} If the new build does not come up it is put back "
            f"automatically."
        )
        return tuple(kept)

    def _old_build_by_id(self, ctx: StageContext, refs: Sequence[str]) -> dict[str, str] | None:
        """What to tag as each ref's rollback when the names are not all there, or None (T170).

        Each ref's source is the image id this install's containers were made
        from it with (`Seams.project_container_images`), or the ref itself
        when no container holds it and its name is still on the daemon. None
        when no whole, consistent build can be named that way: no containers,
        a ref neither a container nor a name answers for, two containers made
        from one ref running different images, or an id the daemon no longer
        has. Then there is no build to keep, which `_keep_rollback()` says.

        Measured on a test box with a compose stand-in (2026-09-28): after
        `docker rmi` of the tag, `docker inspect <container>` still gives the
        ref as `.Config.Image` and the `sha256:` id as `.Image`, the id is still
        on the daemon, and `docker tag <id> <ref>-rollback` works.

        Raises:
            InstallerError: Docker would not say what the containers run
                (`None`, not `()`): nothing has been tagged or started yet.
        """
        project = composegen.project_name(
            self.entry.id,
            ctx.server_dir,
            platform_id=self._seams.platform_id,
            install_id=self._install_id(ctx.server_dir),
        )
        found = self._seams.project_container_images(project)
        if found is None:
            # Codex, round 3: "could not ask" is not "no containers". Those
            # containers may hold the build the server runs, and a compile with
            # no rollback on an unanswered question would overwrite it.
            raise InstallerError(
                "Docker would not say which build this server's containers run, so the "
                "rebuild did not start; nothing was changed. Press "
                f"{server_build_presses.under_server_build(server_build_presses.REBUILD)} "
                "again once Docker answers."
            )
        if not found:
            logger.info(f"{project}: no containers to keep a rollback from")
            return None
        held: dict[str, set[str]] = {}
        for _container, made_from, image_id in found:
            if made_from in refs:
                held.setdefault(made_from, set()).add(image_id)
        sources: dict[str, str] = {}
        for ref in refs:
            ids = held.get(ref, set())
            if len(ids) > 1:
                logger.info(f"{project}: {ref}'s containers run {sorted(ids)}; no one build")
                return None
            if ids:
                (image_id,) = ids
                if self._seams.images_built([image_id]) is not True:
                    logger.info(f"{project}: {ref}'s image {image_id} is not on the daemon")
                    return None
                sources[ref] = image_id
            elif self._seams.images_built([ref]) is True:
                sources[ref] = ref
            else:
                logger.info(f"{project}: nothing holds {ref}, by name or by a container")
                return None
        return sources

    def _restore_rollback(
        self,
        ctx: StageContext,
        refs: Sequence[str],
        kept: Sequence[str],
        touched: bool,
        failure: str,
        *,
        servers_down: ServersDownWork | None = None,
        press: str = server_build_presses.REBUILD,
        parking: _Parking | None = None,
        hold_rollback: bool = False,
    ) -> Generator[str, None, str]:
        """Put the old build back after a compile that finished and a server that did not.

        `parking` (T224): with no container replaced, the build that finished is
        kept as `<ref>-parked` when it is whole and the folder still fingerprints
        as it did before the compile (`_park()`); None keeps nothing.

        `press` is the entry the player pressed, named in the sentence that says what
        pressing it again does (T223).

        `servers_down.back()` (T179's update route) runs once the tags are back and
        before the old build starts, with no Cancel -- a rollback is finished, not
        given up -- and a failure of it is added to the sentence, not raised.

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
        # T225 (scoped re-review): while a stopped build may still land, the
        # `-rollback` names stay on every exit, so every Start can compare with them.
        letting_go: Sequence[str] = () if hold_rollback else kept
        spec = self.entry.container_spec()
        last_words = ""
        if touched:
            # Without terminal colour codes (#305): read live as "[0m[36m311/500 Bot
            # Mahuni logged in" in the press's closing message.
            printed = ansi.strip(self._seams.world_output(spec).text).strip().splitlines()
            last_words = "\n".join(printed[-5:])
            # T158, round 3: the failed build's servers go down BEFORE a single tag
            # moves back -- a tag moved under a running container names a binary
            # it is not running. Its world may still be loading and deaf; then
            # this waits, says so, and "Stop now anyway" means "stop it
            # regardless". The Cancel that brought us here no longer does (T247
            # review): a plain Stop never kills a loading world (T158).
            yield ROLLBACK_STOPPING
            control = _stop_control(ctx, rollback=True)

            def stop_it(say: docker.OutputSink) -> None:
                self._seams.stop_servers(spec, ctx.server_dir, control=replace(control, say=say))

            try:
                yield from _with_hint(_speaking(stop_it, control.abandon), ROLLBACK_WAIT_HINT)
            except docker.DockerCommandError as exc:
                return _NotPutBack(
                    f"{failure} Putting the build from before this rebuild back was not "
                    f"attempted, because the new build's servers could not be stopped ({exc}); "
                    f"the tags still name the new build, all of them. {self._old_images(kept)}",
                    touched=touched,
                )
        else:
            yield "Putting the build from before this rebuild back."
            # T223 (cold review): the same bounded wait as the recreate's, because the
            # usual reason to be here is that Docker did not answer it, and a tag asked
            # of a silent Docker fails and leaves the live tags on the new build. Not
            # stoppable, like every restore. In the touched arm the servers' stop
            # above has just had its answer.
            waited = yield from self._wait_for_docker(
                ctx,
                saying=(
                    "Docker did not answer. Waiting up to 3 minutes for Docker before putting "
                    "the build from before this rebuild back on its tags. Stop does not end "
                    "this wait: until the tags are back, a Start would run the new build."
                ),
                stoppable=False,
            )
            if waited is not None:
                return _NotPutBack(
                    f"{failure} Putting the build from before this rebuild back on its tags was "
                    f"not possible either: Docker did not answer for another "
                    f"{_spell_elapsed(waited)}. No container was replaced, so the server is "
                    f"still running the build it had if it is up, but the image tags name the "
                    f"new build, which has never started; the old images are on the daemon "
                    f"under their {ROLLBACK_TAG_SUFFIX} tags.",
                    touched=False,
                    untested=True,
                )
        # The new build gets its own name FIRST, so a retag that fails part-way
        # can be undone onto it (`FAILED_TAG_SUFFIX`). If even that fails,
        # nothing has moved yet and the sentence below is already true.
        failed = [ref + FAILED_TAG_SUFFIX for ref in refs]
        named: list[str] = []
        for ref, name in zip(refs, failed, strict=True):
            problem = self._seams.tag_image(ref, name)
            if problem:
                yield from self._release(named)
                return _NotPutBack(
                    f"{failure} Putting the build from before this rebuild back was not "
                    f"attempted, because the new build could not be given a name to undo "
                    f"onto ({problem}); the tags still name the new build, all of them. "
                    f"{self._old_images(kept)}",
                    touched=touched,
                    # T223 (lead, under owner answer D1): untouched, the new build never
                    # started, so no Start may run it.
                    untested=not touched,
                )
            named.append(name)
        moved: list[str] = []
        for ref, back in zip(refs, kept, strict=True):
            problem = self._seams.tag_image(back, ref)
            if problem:
                undone = [r for r in moved if not self._seams.tag_image(r + FAILED_TAG_SUFFIX, r)]
                mixed = [r for r in moved if r not in undone]
                yield from self._release(named)
                if mixed:
                    return _NotPutBack(
                        f"{failure} Putting the build from before this rebuild back failed "
                        f"part-way ({problem}) and undoing it failed too, so the tags are "
                        f"MIXED: {', '.join(mixed)} name the old build and the rest name the "
                        f"new one. Do not start this server until they agree. "
                        f"{self._old_images(kept)}",
                        touched=touched,
                        mixed=True,
                    )
                return _NotPutBack(
                    f"{failure} Putting the build from before this rebuild back failed "
                    f"({problem}), and the {len(undone)} tag(s) already moved were moved back, "
                    f"so the tags still name the new build, all of them. "
                    f"{self._old_images(kept)}",
                    touched=touched,
                    untested=not touched,
                )
            moved.append(ref)
        # T224 (owner D1): with no container replaced, the build that finished is
        # kept under `-parked` names BEFORE the `-failed` names go, because those are
        # its only names until then and letting them go deletes it.
        still_kept = parking is not None and parking.still_kept
        not_kept = self._park(ctx, refs, kept, parking) if not touched and not still_kept else ""
        held = yield from self._release(named)
        if not touched:
            yield from self._release(letting_go)
            again = server_build_presses.under_server_build(press)
            if still_kept:
                # The failure says the kept build was not used and is still kept.
                return (
                    f"{failure} No container was replaced: the server is still on the build it "
                    f"had before this rebuild."
                )
            if not not_kept:
                return (
                    f"{failure} The build that had just finished is kept, and no container was "
                    f"replaced: the server is still on the build it had before this rebuild. "
                    f"Start does not use the kept build; pressing {again} again uses it "
                    f"instead of compiling, if the files it was made from have not changed."
                )
            # T223 (owner D1): said, because it is what the press cost. The `-failed`
            # names were the new build's only names, so letting them go deleted it.
            gone = "could not be used" if held else "was removed"
            return (
                f"{failure} The build that had just finished {gone}, {not_kept}, and no "
                f"container was replaced: the server is still on the build it had before this "
                f"rebuild, and pressing {again} again compiles it again."
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
            "is NOT put back by this, so the old build is running on the database as the new "
            "one left it."
        )
        back_failed = ""
        whole = False
        stay: str | None = None
        stopped_database = ROLLBACK_LEFT_STOPPED_DATABASE
        if servers_down is not None:
            try:
                yield from servers_down.back(replace(ctx, cancel=None))
            except LeaveStopped as exc:
                # T217: what the old build would start on could not be put back
                # (the copy of its databases, a source folder). It is not started.
                stay = str(exc)
            except (InstallerError, OSError) as exc:
                back_failed = f"\n{exc}"
            put_back = servers_down.database()
            if put_back is not None:
                # T217: the update route put its copy back (or never started the
                # new build), so the sentence says that instead of "NOT put back".
                database = f"\n{put_back}"
                stopped_database = database
                whole = not back_failed
            database = f"{database}{back_failed}"
        # Asked on every rollback, not only the update route's (T197 fix round 8): a
        # Rebuild pressed to repair MIXED tags (`START_REFUSED_FILE`) keeps those mixed
        # tags as its rollback, and a repair that failed must not start them again.
        refused = self.start_refusal(ctx.server_dir)
        if stay is not None:
            yield from self._release(named)
            yield from self._release(letting_go)
            also = f" {refused}" if refused is not None else ""
            return _LeftStopped(
                f"{failure} The build from before this rebuild was put back, and its servers "
                f"were left STOPPED: {stay}{also}{said}{back_failed}"
            )
        if refused is not None:
            # T179 (lead ruling): the old build is not started on world tables
            # its rollback could not all put back, nor on mixed tags. Its servers
            # stay stopped and the tags name it; the finish or the Rebuild starts
            # it. Said without "running" anywhere: nothing of this server runs now.
            yield from self._release(named)
            yield from self._release(letting_go)
            return _LeftStopped(
                f"{failure} The build from before this rebuild was put back, and its servers "
                f"were left STOPPED: {refused}{said}{stopped_database}"
                f"{back_failed}"
            )
        try:
            yield from self.stage_recreate(ctx, rollback=True)
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
            yield from self._release(named)
            yield ROLLBACK_WAIT_UNSTOPPABLE
            # With no cancel (T247): the Stop that brought a rollback here is
            # already set, and handed on it would end this wait before it began.
            # A rollback is finished, not given up -- `servers_down.back()` above
            # is run the same way -- and this wait is how it learns whether the
            # build it put back is up.
            yield from self.wait_for_ready(replace(ctx, cancel=None), self._native().ready)
        except InstallerError as second:
            # T79. Every ref above is back on the old build -- `moved` is all of
            # them or this line is not reached -- so the `-rollback` names are
            # now second names for images the live tags already point at, and
            # letting them go removes a name rather than the last copy of
            # anything. Until T79 this one exit kept them: a restore whose
            # server did not come up left four `-rollback` tags on the daemon
            # and said nothing about them, which is the same leak the success
            # path had and the harder one to notice, because the press was
            # already reporting a failure.
            yield from self._release(named)
            yield from self._release(letting_go)
            if isinstance(second, _DockerSilentForRestart):
                # T223 (cold review): never started, so not "did not report ready",
                # and nothing of it runs on the database yet.
                return _LeftStopped(f"{failure} {second}{said}{stopped_database}{back_failed}")
            down = servers_down.did_not_come_back() if servers_down is not None else None
            if down is not None:
                # T217 live proof, item 5: the old build crash-looped on the
                # databases put back. Its servers are stopped rather than left
                # restarting, and the sentence names the copies that can restore
                # it, never "starts on the databases it knows" -- and "stopped"
                # only once they are (scoped re-review of c5bf1b67).
                not_stopped = yield from self._stop_the_old_build(ctx)
                # Said once when the old build failed as the new one did: the
                # re-live of 2026-10-05 printed the same crash-loop sentence twice.
                why = ", for the same reason." if str(second) in failure else f": {second}"
                said_down = (
                    f"{failure} The build from before this rebuild was put back, but it did "
                    f"not come up either{why}{said}\n{down(not_stopped)}{back_failed}"
                )
                if not_stopped is not None:
                    return _NotStopped(said_down)
                return _LeftStopped(said_down)
            return _NotUpEither(
                f"{failure} The build from before this rebuild was put back, but it did not "
                f"report ready either: {second}{said}{database}{self._left_running(spec)}"
            )
        yield from self._release(letting_go)
        if servers_down is not None:
            servers_down.came_back()
        ending = (
            f"{failure} The build from before this rebuild was put back and is running "
            f"again.{said}{database}"
        )
        # T247 live review: the old build is up AND its databases went back (T217)
        # -- nothing of the press is left -- so a Stop that led here reads "Stopped".
        return _PutBackWhole(ending) if whole else ending

    def _left_running(self, spec: docker.ContainerSpec) -> str:
        """`ROLLBACK_LEFT_RUNNING` on its own line when the world is still up, else "" (T391).

        Asked of Docker, not assumed: a world that exited is not "still up", and
        a Docker that did not answer is not asked to be believed either way.
        """
        status = self._seams.world_output(spec).status
        return f"\n{ROLLBACK_LEFT_RUNNING}" if status in ("running", "restarting") else ""

    def _stop_the_old_build(self, ctx: StageContext) -> Generator[str, None, str | None]:
        """Stop the old build that did not come up after a rollback (T217). Never raises.

        Returns None once its servers are stopped, else why they could not be, so
        the press never claims a stop it did not make. A world that restarts while
        the stop waits for it to load (CMaNGOS) is a crash loop that never loads,
        so its restart ends the wait (T159's control), and the wait's hint goes
        with the panel's "Stop now anyway".
        """
        spec = self.entry.container_spec()
        yield OLD_BUILD_STOPPING
        control = replace(_stop_control(ctx, rollback=True), restart_ends_the_wait=True)

        def stop_it(say: docker.OutputSink) -> None:
            self._seams.stop_servers(spec, ctx.server_dir, control=replace(control, say=say))

        try:
            yield from _with_hint(_speaking(stop_it, control.abandon), OLD_BUILD_WAIT_HINT)
        except docker.DockerCommandError as exc:
            logger.warning(f"could not stop the old build of {self.entry.id}: {exc}")
            return str(exc)
        return None

    def _old_images(self, kept: Sequence[str]) -> str:
        """Where the build from before is, ASKED rather than assumed (m910q P9).

        A rollback that stopped early said "the old images are on the daemon
        under their -rollback tags" right after Docker had answered "No such
        image: ...-rollback": the names had been removed out of band. So the
        daemon is asked, and the sentence says what it answered.
        """
        gone = [ref for ref in kept if self._seams.images_built([ref]) is False]
        if gone:
            return (
                f"Docker no longer has {', '.join(gone)}, so the build from before this "
                f"rebuild cannot be put back from them."
            )
        if any(self._seams.images_built([ref]) is None for ref in kept):
            return (
                f"Yu'lon could not ask Docker whether the old images are still under their "
                f"{ROLLBACK_TAG_SUFFIX} tags."
            )
        return f"The old images are on the daemon under their {ROLLBACK_TAG_SUFFIX} tags."

    def _use_or_clear_the_kept_build(
        self,
        server_dir: Path,
        refs: Sequence[str],
        kept: Sequence[str],
        parking: _Parking,
    ) -> Generator[str, None, bool]:
        """Use the kept build instead of compiling, or remove it first (T224). True when used.

        Used only on an exact match (owner, D2): a record, F0 (`parking.fingerprint`,
        taken just now) equal to the one it records, and every `<ref>-parked` name
        holding the image id it records. Then the live tags move onto it, the record
        goes and the `-parked` names with it, and the press goes on exactly as after a
        compile: recreate, wait for ready, and the rollback kept above if it fails.
        A tag Docker refuses part-way raises with the kept build untouched, and the
        restore puts back the tags that moved.

        Anything else removes the kept build BEFORE the compile, which needs the disk
        it holds: its `-parked` names that are still there, and the record. That is
        also the sweep of names a crash left without a record.
        """
        record = read_parked_build(server_dir)
        names = [ref + PARKED_TAG_SUFFIX for ref in refs]
        why = ""
        if record is not None:
            if parking.fingerprint is None:
                why = "Yu'lon could not read every file in this folder to compare"
            elif parking.fingerprint != record.fingerprint or set(record.images) != set(refs):
                why = "the files in this folder have changed since it was made"
            elif any(
                self._seams.image_id(name) != record.images[ref]
                for ref, name in zip(refs, names, strict=True)
            ):
                why = "its images are no longer on Docker under the names it was kept as"
        if record is not None and not why:
            parking.still_kept = True
            parking.tagging = True
            for ref, name in zip(refs, names, strict=True):
                problem = self._seams.tag_image(name, ref)
                if problem:
                    raise InstallerError(
                        f"The build kept from {record.when()} could not be put on this "
                        f"server's tags ({problem}), so it was not used. The kept build is still "
                        f"kept."
                    )
            parking.still_kept = False
            parking.made_unix = record.made_unix
            forget_parked_build(server_dir)
            yield from self._release(names)
            back = (
                "the build you have now is kept to put back if it does not come up"
                if kept
                else "there is no build from before to put back if it does not come up"
            )
            yield (
                f"The build kept from {record.when()} was made from exactly the files in this "
                f"folder now (source, modules, patches and build recipe), so it is used instead "
                f"of compiling again. It has never been started: it is started now and waited "
                f"for like any new build, and {back}."
            )
            return True
        if record is None:
            # Names a crash left without a record: asked first, so a server with
            # none (every rebuild's case) issues no removal at all.
            present = [name for name in names if self._seams.image_id(name) is not None]
            if present:
                logger.info(f"rebuild of {self.entry.id}: removing unrecorded kept names {present}")
            yield from self._release(present)
            forget_parked_build(server_dir)
            return False
        # Every name, not only those an id answered for: an unanswered Docker is not
        # "gone" (Codex adversarial review), and the record goes only once none is
        # left, so a name Docker kept is still one the next press can find.
        left = yield from self._release(names)
        if left:
            yield (
                f"A finished build from {record.when()} was kept, but {why}, so it is not used. "
                f"Docker would not remove it yet, so it stays recorded and the next rebuild tries "
                f"again. The server is compiled again."
            )
            return False
        forget_parked_build(server_dir)
        yield (
            f"A finished build from {record.when()} was kept, but {why}, so it was removed "
            f"and the server is compiled again."
        )
        return False

    def _forget_the_stopped_build(self, server_dir: Path) -> str:
        """Remove `STOPPED_BUILD_FILE`; "" once gone, else a sentence (logged) saying so.

        A record that outlives its `-rollback` names makes every Start check
        half-answered, so it refuses (`STOPPED_BUILD_UNCHECKED_REFUSAL`) until the
        next rebuild that succeeds removes it -- said, not hidden (scoped re-review).
        """
        forgot = forget_stopped_build(server_dir)
        if not forgot:
            return ""
        logger.warning(f"rebuild of {self.entry.id}: {forgot}")
        return (
            f" A stopped rebuild's record is out of date, but {forgot}; until it is removed "
            f"every Start is refused, and the next rebuild removes it."
        )

    def _remember_the_stopped_build(
        self, server_dir: Path, refs: Sequence[str], parking: _Parking
    ) -> str:
        """Write `STOPPED_BUILD_FILE` for a compile that was stopped (T225). "" or a warning."""
        wrote = remember_stopped_build(
            server_dir,
            StoppedBuild(
                tuple(refs),
                parking.fingerprint if self._seams.distro is None else None,
                int(time.time()),
            ),
        )
        if wrote:
            logger.warning(f"rebuild of {self.entry.id}: {wrote}")
            again = server_build_presses.under_server_build(server_build_presses.REBUILD)
            return f" But {wrote}, so Start cannot check; press {again} before starting."
        return ""

    def _settle_stopped_build(
        self, server_dir: Path, refs: Sequence[str]
    ) -> Generator[str, None, None]:
        """Put the tags back after a stopped build that landed, and keep it if it can (T225).

        Runs at the start of every rebuild, Update and Return, before the rollback is
        kept. With no `STOPPED_BUILD_FILE` it asks nothing. A ref moved when it and its
        `-rollback` name both answer and differ. A whole landed build is kept as
        `<ref>-parked` with the fingerprint the stopped press took before it compiled
        (`_park()`'s write order), so the build stage may use it; a partial one is not.
        Every moved tag then goes back onto its `-rollback` name. A tag Docker will not
        move back raises with the record kept, so Start stays refused.
        """
        record = read_stopped_build(server_dir)
        if record is None:
            return
        moved: dict[str, str] = {}
        for ref in refs:
            now = self._seams.image_id(ref)
            before = self._seams.image_id(ref + ROLLBACK_TAG_SUFFIX)
            if now is not None and before is not None and now != before:
                moved[ref] = now
        if not moved:
            # Kept until this press succeeds (scoped re-review): the solve may still
            # be running, and a press that fails must leave the Start check armed.
            logger.info(f"rebuild of {self.entry.id}: the stopped build has not landed")
            return
        # The lead's belt (scoped re-review): no tag moves under a running server
        # (T158's order), and "could not say" is not "down".
        spec = self.entry.container_spec()
        running = [self._seams.ask_world_running(name) for name in (spec.world, spec.auth)]
        again = server_build_presses.under_server_build(server_build_presses.REBUILD)
        if any(answer is True for answer in running):
            raise InstallerError(
                "A rebuild stopped earlier finished in Docker's background and moved this "
                "server's image tags, and this server is running. Yu'lon does not move image "
                f"tags under a running server: Stop the server, then press {again} again. "
                "Nothing was changed."
            )
        if any(answer is None for answer in running):
            raise InstallerError(
                "A rebuild stopped earlier finished in Docker's background and moved this "
                "server's image tags, and Yu'lon could not tell whether this server is running, "
                f"so it moved nothing. Once Docker answers, press {again} again. Nothing was "
                "changed."
            )
        kept = len(moved) == len(refs) and self._park_landed(server_dir, refs, moved, record)
        for ref in moved:
            problem = self._seams.tag_image(ref + ROLLBACK_TAG_SUFFIX, ref)
            if problem:
                raise InstallerError(
                    f"A rebuild stopped earlier finished in Docker's background and moved this "
                    f"server's image tags onto its build, and Docker would not move {ref} back "
                    f"({problem}). Nothing was compiled; Start stays refused. Press "
                    f"{server_build_presses.under_server_build(server_build_presses.REBUILD)} "
                    f"again once Docker answers."
                )
        yield (
            "A rebuild stopped earlier finished in Docker's background and moved this server's "
            "image tags onto its build, which never started. The tags are back on the build "
            "this server runs; "
            + (
                "the finished build is kept, and this rebuild uses it if the folder has not "
                "changed since."
                if kept
                else "the finished build is not kept."
            )
        )

    def _park_landed(
        self,
        server_dir: Path,
        refs: Sequence[str],
        moved: Mapping[str, str],
        record: StoppedBuild,
    ) -> bool:
        """Keep a whole landed build as `<ref>-parked` with its record (T225). True if kept."""
        if self._seams.distro is not None or record.fingerprint is None:
            return False
        if forget_parked_build(server_dir):
            return False
        names: list[str] = []
        for ref in refs:
            name = ref + PARKED_TAG_SUFFIX
            if self._seams.tag_image(ref, name):
                self._let_go(names)
                return False
            names.append(name)
        if any(self._seams.image_id(ref + PARKED_TAG_SUFFIX) != moved[ref] for ref in refs):
            self._let_go(names)
            return False
        if remember_parked_build(
            server_dir,
            ParkedBuild(record.fingerprint, dict(moved), record.made_unix, __version__),
        ):
            self._let_go(names)
            return False
        return True

    def _park(
        self,
        ctx: StageContext,
        refs: Sequence[str],
        kept: Sequence[str],
        parking: _Parking | None,
    ) -> str:
        """Keep the build that just finished as `<ref>-parked` (T224). "" if kept, else why not.

        Called in a restore's untouched arm once every live tag is back on the
        build from before, while the new build still has its `-failed` names.
        Kept only when every one of these holds, and the reason is the first that
        does not:

        * the server is not inside a WSL distro (owner, D4);
        * F0, taken before the compile, has an answer;
        * the build is whole: every ref's `-failed` name holds an image that is not
          its `-rollback` image (a Stop part-way tags some refs only);
        * the folder fingerprinted as F0 again when the build stage ended
          (`_Parking.after`), so nothing changed while it compiled;
        * the old record goes, then every `-parked` name is made and holds the
          image its `-failed` name does, then the new record is written. A crash
          part-way leaves names with no record, which the next rebuild removes,
          and never a record naming another build. A failure past the first tag
          lets the `-parked` names go again.

        Yields nothing: the caller's restore says what happened, once.
        """
        if self._seams.distro is not None:
            return "because a server inside WSL is not kept that way yet"
        if parking is None or parking.fingerprint is None:
            return "because Yu'lon could not read every file it was made from"
        made: dict[str, str] = {}
        for ref, back in zip(refs, kept, strict=True):
            new = self._seams.image_id(ref + FAILED_TAG_SUFFIX)
            if new is None or new == self._seams.image_id(back):
                return "because Docker had not named every image of it yet"
            made[ref] = new
        if parking.after != parking.fingerprint:
            return "because files in the server folder changed while it compiled"
        forgot = forget_parked_build(ctx.server_dir)
        if forgot:
            return f"because {forgot}"
        names: list[str] = []
        for ref in refs:
            name = ref + PARKED_TAG_SUFFIX
            problem = self._seams.tag_image(ref + FAILED_TAG_SUFFIX, name)
            if problem:
                self._let_go(names)
                return f"because Docker would not name it to keep it ({problem})"
            names.append(name)
        if any(self._seams.image_id(ref + PARKED_TAG_SUFFIX) != made[ref] for ref in refs):
            self._let_go(names)
            return "because its kept names did not hold it when Docker was asked"
        wrote = remember_parked_build(
            ctx.server_dir,
            ParkedBuild(
                fingerprint=parking.fingerprint,
                images=made,
                made_unix=parking.made_unix,
                app=__version__,
            ),
        )
        if wrote:
            self._let_go(names)
            return f"because {wrote}"
        logger.info(f"rebuild of {self.entry.id}: the finished build is kept as {names}")
        return ""

    def _compose_tagged(
        self, refs: Sequence[str], kept: Sequence[str], *, unanswered_moved: bool
    ) -> bool:
        """Whether this press's build moved a live tag, read off the daemon (T225).

        With a rollback kept, a ref moved when its image is not its `-rollback`
        name's image. An id Docker does not give counts as moved when
        `unanswered_moved` -- the press ran something that tags (`rebuild()`'s
        `may_have_tagged()`): putting a tag back that never moved costs a retag onto
        the image it already names, while trusting the silence lets the only copy of
        the old build go. Otherwise it counts as not moved (cold review): a compile
        that failed finished no build, and a restore over it would wait for Docker,
        say the tags name a new build and refuse Start over a build that never
        existed. With
        none kept (T170) there is nothing to compare against, and the build moved
        the tags when every image is now there. Never raises, and yields nothing:
        the `BaseException` path asks it too.
        """
        if not kept:
            try:
                return self._seams.images_built(refs) is True
            except Exception as exc:  # noqa: BLE001 - a question, never a new failure
                logger.warning(f"could not ask whether {refs} exist: {exc}")
                return False
        for ref in refs:
            try:
                now = self._seams.image_id(ref)
                before = self._seams.image_id(ref + ROLLBACK_TAG_SUFFIX)
            except Exception as exc:  # noqa: BLE001 - an unanswered question
                logger.warning(f"could not read the image {ref} names: {exc}")
                if unanswered_moved:
                    return True
                continue
            if now is None or before is None:
                if unanswered_moved:
                    return True
            elif now != before:
                return True
        return False

    def _let_go(self, kept: Sequence[str]) -> tuple[str, ...]:
        """Take the transient names off the daemon. Returns the ones still there.

        **T79, and the reason the answer had to stop being `None`.** This asked
        once and threw the refusal away. `docker.remove_image()` is deliberately
        soft -- it logs and returns the daemon's words -- so a name docker
        declined to remove was left on the daemon with nothing said anywhere a
        user or a gate would look. The round-3 gate then found a stale
        `ac-wotlk-db-import:...-rollback` after two clean rebuilds, and the
        earlier failed build had left a `client-data` one the same way.

        It is not luck which two: the rebuild's recreate is `compose up
        --force-recreate --no-deps` over `spec.compose_services()`, which never
        selects the one-shot import or the client-data job, so their EXITED
        containers still reference the pre-rebuild images when these names are
        let go. `-rollback` is by then the image's only name, so removing it
        removes the image, and the daemon refuses: `conflict: unable to delete
        <id> (must be forced) - container <id> is using its referenced image`.
        Every rebuild of this shape leaks two names, which is what "a leftover,
        not a rollback" in the gate actually was.

        So the refusal is READ. `must be forced` is the daemon saying the only
        thing in the way is a container that is not running, and the second ask
        carries `-f`, which takes the NAME off and leaves the layers the exited
        container holds. Anything else -- `cannot be forced`, a read-only layer
        store, a daemon that went away -- is returned to the caller to say out
        loud, because a name this module documents as transient sitting on the
        daemon for ever is a thing the user has to be told, not a thing to
        retry blind.
        """
        left: list[str] = []
        for back in kept:
            problem = self._seams.remove_image(back)
            if problem and _MUST_BE_FORCED in problem:
                logger.info(f"{back} is held by a stopped container; asking again with --force")
                problem = self._seams.remove_image(back, force=True)
            if problem:
                logger.warning(f"{back} is still on the daemon: {problem}")
                left.append(back)
        return tuple(left)

    def _release(self, kept: Sequence[str]) -> Generator[str, None, tuple[str, ...]]:
        """`_let_go()`, with a sentence for whatever the daemon would not take. Returns those.

        Used on every exit that can still speak. The sentence is not a failure --
        the rebuild's own verdict is decided elsewhere and is not changed by a
        name -- it is the one place a leaked tag becomes visible to the person
        who would otherwise find it in `docker images` months later.
        """
        left = self._let_go(kept)
        if left:
            logger.warning(
                f"to remove the rebuild's leftover names once the server is stopped: "
                f"docker image rm -f {' '.join(left)}"
            )
            yield (
                f"Docker would not take {len(left)} transient name(s) off the daemon: "
                f"{', '.join(left)}. They are this rebuild's own bookkeeping and nothing "
                f"needs them; the log says what docker objected to, and how to remove them once "
                f"this server is stopped."
            )
        return left

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
        if (
            missing == [composegen.BASE_FILE]
            and self._checkout_is_the_server_dir()
            and self._replaceable_compose(server_dir)
        ):
            # T170 cold review SF2: the repository's own file, untouched, beside
            # Yu'lon's other two -- what a failed Update leaves. Git is asked
            # (a container) because this runs on the press's worker, never the
            # GUI thread. The Repair makes its own checks of the record and the
            # containers; this names it, and it may still refuse.
            raise InstallerError(
                f"{server_dir / composegen.BASE_FILE} is the one that came with the server's "
                "source code, not Yu'lon's -- an update that could not finish leaves it there "
                "-- and Yu'lon does not build or start this server from it. Nothing was started. "
                f"{compose_back_advice(server_dir, once_fixed=False)}"
            )
        if missing:
            raise InstallerError(
                f"{server_dir} is missing the compose files Yu'lon builds with, or they were "
                f"not written by Yu'lon: {', '.join(missing)}. Nothing was started. "
                f"{composegen.BUILD_FILE} is the only file that carries the build "
                f"instructions — compose never loads it on its own, so without it a build "
                f"here builds nothing and reports success — and this "
                f"app will not overwrite a compose file it did not write, because doing that "
                f"can orphan the volumes your characters are in. A server built by another "
                f"launcher has to be rebuilt by that launcher."
            )
        return state

    def _refuse_sources_off_their_build(
        self, server_dir: Path, state: InstallState
    ) -> tuple[str, ...]:
        """Refuse a Rebuild whose source folders are not where the running build came from (T217).

        "Where it came from" is the install record's `source_revs[].built`, which
        only an update writes. With no record the folder is compared with this
        Yu'lon's pin, and a difference is NOT refused: an install records nothing
        and pins move between releases, so it is a server installed on an older
        pin. The line returned says so and names Update to latest, which moves
        the sources and the databases together. It never advises checking out the
        pin, which would compile the new core over the old databases (the T220
        crash; cold review of 23361ca3).

        A folder git will not read is not refused either -- Rebuild is the repair
        press, and an unreadable checkout must not lock it out (the owner's answer
        of 2026-10-04) -- and the lines returned say it could not be checked. One
        `rev-parse` per moving source.

        Raises:
            InstallerError: a source's HEAD is not the commit the record says its
                build came from; names the folder, both commits and the command
                that puts it back.
        """
        unchecked: list[str] = []
        update = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
        for source in self.sources_that_move():
            dest = server_dir / source.dest
            recorded = state.rev_for(source.repo)
            built = recorded.built.split()[0] if recorded is not None and recorded.built else ""
            expected = built or (source.rev or "")
            if not expected:
                continue
            head = self._seams.head_sha(dest)
            if head is None:
                unchecked.append(
                    f"Yu'lon could not check which commit {dest} is on, so it is rebuilt as it "
                    f"stands; it should be on {expected[:7]}."
                )
                continue
            if not built:
                if not head.startswith(expected):
                    unchecked.append(
                        f"{dest} is on {head[:7]}, an older pin than the {expected[:7]} this "
                        f"version of Yu'lon ships. It is compiled as it stands. {update} "
                        "moves every source to the new pin safely, the databases included."
                    )
                continue
            if not head.startswith(expected):
                raise InstallerError(source_off_its_build(dest, head, expected))
        return tuple(unchecked)

    def rebuild_refusal_before_asking(self, server_dir: Path) -> str | None:
        """What `_refuse_sources_off_their_build()` would refuse, asked before the question.

        T217 live proof (item 3): Rebuild asked its whole question -- an hour's
        compile, the server down -- and then refused in no time. The view asks
        this first, and it refuses ONLY where the press would (scoped re-review):
        `.git/HEAD` is read first (`read_head_file()`, no git run), and only a
        folder it says is off its build is asked again of the press's own reader
        (`Seams.head_sha`). Where git cannot answer the press goes on, and so
        does this: None. No record or no recorded build is None too.
        """
        state = read_state(server_dir, valid=self.stage_names())
        if state is None:
            return None
        for source in self.sources_that_move():
            recorded = state.rev_for(source.repo)
            built = recorded.built.split()[0] if recorded is not None and recorded.built else ""
            if not built:
                continue
            dest = server_dir / source.dest
            seen = read_head_file(dest)
            if seen is None or seen.startswith(built):
                continue
            head = self._seams.head_sha(dest)
            if head is not None and not head.startswith(built):
                return source_off_its_build(dest, head, built)
        return None

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

    def _locking(
        self, stages: Sequence[Stage], server_dir: Path, started_empty: bool
    ) -> tuple[Stage, ...]:
        """`stages`, each locking the server folder to this Windows account first (T174).

        Before every stage rather than once after the claim: WotLK's
        `clone-core` clones into the server folder, and both clone seams begin
        by removing a destination with no `.git`, so the folder the claim was
        locked on is gone by the second stage and the clone's is a new one. A
        stage that finds the folder already locked costs one read. The first
        secret -- CMaNGOS's `.db_password` in `db-password`, `.env` in
        `generate-compose` -- is written inside a stage, and so after its lock.

        Install only. A rebuild, an update or an adoption goes through
        `_staged()` without this: changing an existing folder's permissions is
        Repair server files…'s offer (`serverlock.route_for_app()`). Off Windows
        the wrapper asks nothing. A lock that fails says so once and the stages
        run as they did before T174; a later stage asks again only if it finds
        a different folder there, as after WotLK's clone
        (`serverlock.InstallLock`).

        A resume locks the folder it resumes in, and so does a resume of an
        install begun before T174: the folder holds its record, so its first
        stage this time locks what is already there, the secrets an earlier run
        wrote included (`serverlock.py`: the lock is carried onto what inherits).
        A folder that grants a SPECIFIC account or group is left as it is and
        said so (`serverlock.InstallLock`); Repair server files… offers it.

        Only a folder that is ours: one the install `started_empty` in, or one
        holding this install's record. The one other folder a stage meets is
        somebody's own git checkout, which `_guard()` lets through so the clone
        stage can say whose it is -- and refuses there, leaving it untouched
        (`_claim_before_writing()`'s rule), so its permissions are left alone
        too. A checkout the clone stage accepts as this install's gets the
        record after that stage, and the lock before the next.
        """
        folder_lock = serverlock.InstallLock(server_dir)

        def locked(
            body: Callable[[StageContext], Iterator[str]],
        ) -> Callable[[StageContext], Iterator[str]]:
            def run(ctx: StageContext) -> Iterator[str]:
                if started_empty or (server_dir / STATE_FILE).is_file():
                    yield from folder_lock.ensure()
                yield from body(ctx)

            return run

        return tuple(replace(stage, run=locked(stage.run)) for stage in stages)

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
        write_state(server_dir, replace(state, last_error="", error_run=""))

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
        ask = self._seams.install_id
        if ask is not None:
            return ask(server_dir)
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
                f"{self.entry.name} has no native install section, so nothing was started."
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
                "catalog entry has no native install section. Nothing was started."
            )
        if self.entry.install.native.family != self.family:
            raise InstallerError(
                f"{self.entry.name} is catalogued as a {self.entry.install.native.family} "
                f"install but was handed to the {self.family} engine. That is a bug in the "
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
        # told so before being asked for a root password. `run()` does not reuse the
        # state this returns: `_guard()` reads it again, fresh. Here it answers
        # one question, what the free-space rows may leave out (T112, `_spent()`).
        #
        # `_refuse_foreign_containers()` deliberately does NOT move up. It asks
        # which compose project owns a container wearing this entry's names, and
        # there is no answer to that without a daemon.
        state = self._claim_folder(server_dir)
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
            # T250: a Stop comes back from provisioning as a report that is not
            # ready -- and, pressed on the docker-group question, as a consent
            # declined -- never as an error. Read as a refusal it would put
            # "Docker is not available" on screen for a button the player pressed.
            self._check_cancel(cancel)
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
        spent = self._spent(state, server_dir) if facts.docker_ready else preflight.NOTHING_SPENT
        report_checks = preflight.evaluate(self.entry, server_dir, facts, spent)
        yield from preflight.lines(report_checks)
        if not report_checks.ok():
            raise InstallerError(
                "This machine cannot install the server yet:\n" + report_checks.message()
            )

    def _spent(self, state: InstallState, server_dir: Path) -> Spent:
        """What an earlier run of this install already spent, for the free-space rows (T112).

        The T95 gate on m910q (2026-09-24) was refused "21 GB free, and the
        install needs 40 GB" reinstalling WoW TBC into a folder whose build was
        done: preflight judged the disk before anything asked what the press
        would skip. The answer is `build_would_be_skipped()`'s rule, asked
        without a stage context: the record AND the daemon's images, because a
        record alone is a hint (`InstallState.has()`), and a recorded build
        whose images were pruned compiles again and needs the whole floor.

        The daemon is asked only when the record already says `build`, so a
        fresh install and an early resume ask nothing they did not ask before.

        T203: a press that will run the build AGAIN -- every recorded stage
        before it done, and the build not spent -- is credited with the build
        cache Docker GAINED since this folder's build last started
        (`BUILD_CACHE_FILE`), which the build reuses (`Spent.build_cache_bytes`).
        No record of that start, or a cache now smaller than it, credits
        nothing: the rest of the machine's cache may be another server's.

        The same press is also credited with what the server folder already
        holds (`Spent.server_dir_bytes`, `folder_bytes()`): the checkout and
        whatever else the finished stages wrote there, which the m910q live test
        of PR 294 (2026-10-04) found asked for again on one drive.
        """
        if state.has("build") and self._seams.images_built(self.image_refs_at(server_dir)) is True:
            return preflight.Spent(build=True)
        if not self._resumes_at_build(state):
            return preflight.NOTHING_SPENT
        folder = self._seams.folder_bytes(server_dir)
        held = folder if folder is not None and folder > 0 else 0
        baseline = read_build_cache_baseline(server_dir)
        cache = self._seams.build_cache_bytes() if baseline is not None else None
        added = cache - baseline if cache is not None and baseline is not None else 0
        return preflight.Spent(build_cache_bytes=max(added, 0), server_dir_bytes=held)

    def _resumes_at_build(self, state: InstallState) -> bool:
        """Is every recorded stage before `build` in this folder's record? (T203)

        A family with no `build` stage never resumes at one.
        """
        stages = self.stages()
        names = [stage.name for stage in stages]
        if "build" not in names:
            return False
        before = stages[: names.index("build")]
        return all(state.has(stage.name) for stage in before if stage.recorded)

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
                f"{server_dir} already holds an install of {existing.game_id}. Pick another folder."
            )
        if existing is not None and existing.family and existing.family != self.family:
            raise InstallerError(
                f"{server_dir} was installed as {existing.family}, but the catalog now says "
                f"{self.entry.name} is {self.family}. A folder is never reinterpreted: pick "
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
            self.entry.id,
            server_dir,
            platform_id=self._seams.platform_id,
            install_id=self._install_id(server_dir),
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
                "or yours. Continuing would reset it to a fresh copy of that repository, so "
                "nothing was touched. Delete that file if it is left over from an interrupted "
                "install, or install into an empty folder instead."
            )
        raise InstallerError(
            f"{dest} is already a git checkout of {url}, and there is no record here of an "
            "install this app made. Continuing would reset it to a fresh copy of that "
            "repository, which throws away anything you have changed, so nothing was touched. "
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
        self,
        ctx: StageContext,
        sources: Sequence[EmulatorSource],
        *,
        recorded_as: str,
        sparse_exclude: Mapping[str, Sequence[str]] | None = None,
    ) -> Iterator[str]:
        """Clone every source at its `dest`, refusing what is not ours and touching what is done.

        `recorded_as` is the name the FAMILY binds this body to in its `Stage`
        tuple; the body cannot know it, and without it the record could not be
        consulted at all — which is how the AzerothCore stages came to fetch and
        reset on every resume. See `already_cloned()`.

        `sparse_exclude` maps a source's `dest` to the folders its checkout leaves
        out (`git.CloneSpec.sparse_exclude`); the TrinityCore family passes its
        block's `sparse_exclude` for its core checkout (T179). A source not in it is
        checked out whole, as every source was before.

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
                    f"{dest} is a checkout of {existing}, not of {source.url}. Nothing was changed."
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
            yield from self._clone_lines(
                git.CloneSpec(
                    url=source.url,
                    dest=dest,
                    branch=source.branch,
                    sparse_path=source.sparse_path,
                    depth=source.depth,
                    rev=source.rev,
                    sparse_exclude=tuple((sparse_exclude or {}).get(source.dest, ())),
                ),
                recorded_as,
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
        label = self._bind_label(ctx.server_dir)
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
        #
        # `world_env` keeps a live command channel on (T101). A Repair and
        # Update to latest's put-back run this same body over a finished
        # install, and without it the override went back to the pre-channel
        # file: measured on yulon-ubuntu 2026-09-24, the Repair's own `up`
        # brought the world up with `AC_SOAP_ENABLED` gone and nothing on the
        # channel's port. `None` on a first install (no press yet), which is
        # `render()`'s own default.
        #
        # And the player's bot count rides over it (T117): the Bots box writes
        # Min/Max into this same file, and rendering the catalog's 500 again put
        # it back on every Repair and Update. Read off the file being replaced,
        # never probed; no usable pair in it leaves the catalog's number.
        try:
            plan = self._render_compose(ctx.server_dir, ctx.secrets, label)
        except composegen.ComposeGenError as exc:
            raise InstallerError(str(exc)) from exc
        made = self._make_server_folders(ctx.server_dir)
        for path in made:
            yield f"Made {path.name}/ for the server to write into."
        # T171: the zone the override names, copied again from this Yu'lon's
        # own `tzdata` before a compose file binds the folder -- so Docker never
        # makes it as root, and a rule change an update brings reaches the
        # server at this install or Repair. (A CMaNGOS Update to latest no longer
        # runs this stage -- T173 -- and refreshes the file in `stage_recreate`
        # instead.) CMaNGOS only; nothing elsewhere.
        try:
            placed = time_zone.place(self.entry, ctx.server_dir, plan.override)
        except (OSError, time_zone.TimeZoneError) as exc:
            raise InstallerError(
                f"the server's time zone file could not be copied into "
                f"{ctx.server_dir / time_zone.FOLDER}: {exc}. Nothing else was written."
            ) from exc
        for path in placed:
            yield f"Copied the time zone file {path.relative_to(ctx.server_dir).as_posix()}."
        replaceable = self._replaceable_compose(ctx.server_dir)
        if replaceable:
            yield (
                f"Replacing the {composegen.BASE_FILE} that came with the repository; it is "
                "unchanged from what git has, so git can bring it back."
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
                f"{ctx.server_dir} could not be relabelled for containers. If the server "
                "refuses to start under SELinux, run this:\n"
                f"chcon -Rt container_file_t {ctx.server_dir}"
            )

    def _refresh_world_data(self, server_dir: Path) -> str | None:
        """T219: the map-data fingerprint before this engine's own starts; the warning if any.

        Raises:
            InstallerError: it could be neither written nor removed, so the world would
                read map data the server folder no longer holds; nothing was started.
        """
        try:
            return world_data.refresh(self.entry, server_dir)
        except world_data.FingerprintNotRecorded as exc:
            raise InstallerError(str(exc)) from exc

    def _put_back_the_zone_file(self, server_dir: Path) -> str | None:
        """T171: the zone file `Controller.start()` puts back, before this engine's own starts.

        The install's `up` and a rebuild's recreate start containers without
        the controller; the same `time_zone.refresh()`, the same rule: copied
        only when the bytes differ, and a failure is said, never a refusal.
        """
        try:
            with (server_dir / composegen.OVERRIDE_FILE).open(
                encoding="utf-8", newline=""
            ) as handle:
                override = handle.read()
        except (OSError, UnicodeDecodeError):
            return None
        return time_zone.refresh(self.entry, server_dir, override)

    def _make_server_folders(self, server_dir: Path) -> tuple[Path, ...]:
        """Make each folder the compose file binds for the server to write into (T165).

        `composegen.server_folders()` names them: on Tortoise `logs`, `honor` and
        `pdump`, on TBC and Vanilla `logs` (T169) -- or the folder the install's
        conf names instead, once there is a conf to ask. Made HERE, by the app,
        before a compose file that binds them is written, because the
        alternative maker is Docker: a bind whose host folder
        is missing is created by the daemon, which on Linux is root, and a
        root-owned folder holds files the player cannot delete and that stop an
        uninstall's removal of the server folder. Made by the app, the folder is
        the player's; the files a root container writes into it are still
        deletable, since removing a file is a right on the folder.

        Never emptied and never replaced: a folder that is there is left as it
        is, and a file where a folder belongs is refused rather than moved.
        Returns the folders it made.

        Raises:
            InstallerError: a folder could not be made; nothing after it was written.
        """
        try:
            folders = composegen.server_folders(self.entry, server_dir=server_dir)
        except composegen.ComposeGenError as exc:
            raise InstallerError(str(exc)) from exc
        made: list[Path] = []
        for folder in folders:
            path = server_dir / folder.name
            if path.is_dir():
                continue
            try:
                path.mkdir()
            except OSError as exc:
                raise InstallerError(
                    f"{path} could not be made as a folder for the server to write its "
                    f"{folder.name} into ({exc}), so the compose file that binds it was not "
                    "written. If something else is at that name, move it aside and try again."
                ) from exc
            made.append(path)
        return tuple(made)

    def _bind_label(self, server_dir: Path) -> str:
        """`:z` or nothing, for this folder on this host; see `stage_generate_compose`."""
        return platform.bind_label(
            enforcing=self._seams.ask_selinux(), fs_type=self._seams.ask_fs(server_dir)
        )

    def _render_compose(
        self, server_dir: Path, secrets: Secrets, label: str
    ) -> composegen.ComposePlan:
        """The three compose files for this install: the ONE render the install and T106 share.

        Split out of `stage_generate_compose` so the repair cannot render a file the
        install would not: same entry, same templates, same password plan, same bind
        label, same platform seam.

        Raises:
            composegen.ComposeGenError: whatever `composegen.render()` refuses.
        """
        return composegen.render(
            self.entry,
            server_dir,
            templates_root=self.installers_root,
            # A live channel press is kept by every regeneration (T101), and so is
            # the player's bot count off the override on disk (T117). A CMaNGOS
            # tree keeps both in its .conf files, so this is None there.
            world_env=bot_count.world_env(
                self.entry, server_dir, composegen.channel_world_env(self.entry, server_dir)
            ),
            db_password=secrets.db_password,
            bind_label=label,
            platform_id=self._seams.platform_id,
            install_id=self._install_id(server_dir),
            # T171: a new server's clock is this computer's (owner, 2026-09-28).
            # Asked on every render and used only when the folder has no install
            # yet; an installed server's zone is carried off its own override.
            new_install_zone=self._seams.ask_zone(),
        )

    def _base_compose_facts(
        self, server_dir: Path
    ) -> tuple[ComposeCheck, str | None, str | None, tuple[_ConfEdit, ...]]:
        """What the base file is now, and what this version renders for it (T106).

        Only `docker-compose.yml`. The override and the build file are left to the
        routes that already own them, and `.env` holds the password.

        `resolve_secrets()` is asked for the password because `render()` requires
        one. For a CMaNGOS entry the plan is `generated`, so the value reaches only
        the plan's `dotenv` -- never the base text, which spells
        `${DB_ROOT_PASSWORD:?…}` -- and this method never writes the dotenv. Even
        the value it mints when the password file is gone is therefore harmless here.

        **The SELinux label comes from the file, not from the host** (review,
        2026-09-24). The install decided `:z` once, from the host as it was then,
        and wrote it on every host bind. Asking the host again at press time --
        while `getenforce` fails, or with the host briefly permissive -- answers "no
        label", which read an enforcing install as stale and let a repair strip the
        label its containers need. So the label is `composegen.bind_label_of()` of
        the file on disk, and the host is asked only when there is no Yu'lon file to
        read one from. A file whose binds disagree is refused (`mixed`).

        **A conf rides with its bind** (T169). On `stale`, the confs whose folder
        setting the fresh render binds and the file on disk does not are read
        exactly (`_conf_edits()`), and the setting each still leaves at upstream's
        `""` is what the same repair sets; a conf that cannot be read is `error`.

        Returns the check, the fresh render (None when it could not be made), the
        text on disk (None when there is none) -- the snapshot a repair backs up
        and must find unchanged before it writes -- and the conf edits.
        """
        path = server_dir / composegen.BASE_FILE
        name = composegen.BASE_FILE
        try:
            text: str | None = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = None
        except (OSError, UnicodeDecodeError) as exc:
            return (
                ComposeCheck("error", f"{path} could not be read ({exc}). Nothing was written."),
                None,
                None,
                (),
            )
        ours = text is not None and composegen.is_marker_line(text)
        if text is not None and not ours:
            upstream = self._upstream_compose_facts(server_dir, text)
            if upstream is not None:
                # No conf edits: T169's are the CMaNGOS family's (`_conf_edits()`
                # answers nothing for a tree with no `cmangos` block), and this
                # state is only ever a checkout's -- WotLK's.
                check, fresh_base, on_disk = upstream
                return check, fresh_base, on_disk, ()
        label: str | None = None
        if text is not None and ours:
            try:
                label = composegen.bind_label_of(text)
            except composegen.MixedBindLabels as exc:
                return (
                    ComposeCheck(
                        "mixed",
                        f"{path}: {exc}, so Yu'lon cannot tell which way this install was made "
                        "and does not rewrite it. Nothing was written. Give every `- ./` line "
                        "the same ending (`:z` on all of them, or on none) and check again.",
                    ),
                    None,
                    text,
                    (),
                )
        if label is None:
            label = self._bind_label(server_dir)
        try:
            fresh = self._render_compose(server_dir, self.resolve_secrets(server_dir), label).base
        except (composegen.ComposeGenError, InstallerError) as exc:
            return (
                ComposeCheck(
                    "error",
                    f"Yu'lon could not work out what {name} should say for this install: {exc} "
                    "Nothing was written.",
                ),
                None,
                text,
                (),
            )
        if text is None:
            return (
                ComposeCheck(
                    "missing",
                    f"{path} is not there, so there is nothing to repair. Nothing was written.",
                ),
                fresh,
                None,
                (),
            )
        if not ours:
            return (
                ComposeCheck(
                    "foreign",
                    f"{path} does not start with Yu'lon's own first line, so it is somebody's "
                    "own file now and Yu'lon does not rewrite it. Nothing was written.",
                ),
                fresh,
                text,
                (),
            )
        has, wants = composegen.project_of(text), composegen.project_of(fresh)
        if has != wants:
            return (
                ComposeCheck(
                    "moved",
                    f"{path} runs the server as the compose project {has}, and a file written "
                    f"for this folder would name {wants}: the install was moved or copied here "
                    "after it was made. A repair would start the server as a new project, away "
                    "from its characters' database volume, so nothing was written.",
                ),
                fresh,
                text,
                (),
            )
        if composegen.same_compose(text, fresh):
            return ComposeCheck("current"), fresh, text, ()
        if self._checkout_is_the_server_dir():
            # T170 keeps T106's choice for WotLK: its own file follows the app
            # on Update, which writes it again (`_rewrite_what_we_own()`), so
            # the Repair it gained is for the repository's file alone.
            update = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
            return (
                ComposeCheck(
                    "follows",
                    f"{path} is Yu'lon's own and differs from what this version writes; on "
                    f"this server {update} writes it again, and {REPAIR_FILES_LABEL} is "
                    "offered only for the file that came with the repository. Nothing was "
                    "written.",
                ),
                fresh,
                text,
                (),
            )
        try:
            edits, kept = self._conf_edits(server_dir, text)
        except InstallerError as exc:
            return ComposeCheck("error", f"{exc} Nothing was written."), fresh, text, ()
        for why in kept:
            logger.info(f"{self.entry.id}: {why}")
        added, removed = _lines_changed(text, fresh)
        settings = tuple(line for edit in edits for line in edit.settings)
        check = ComposeCheck(
            "stale",
            added=added,
            removed=removed,
            settings=settings,
            kept=kept,
            world_data_gb=self._world_data_added(text, fresh),
        )
        return check, fresh, text, edits

    def _world_data_added(self, text: str, fresh: str) -> float:
        """T219: the room the volume takes, when this repair is the one that adds it; else 0."""
        if not world_data.declares(fresh) or world_data.declares(text):
            return 0.0
        trinitycore = self._native().trinitycore
        return (trinitycore.world_data_gb or 0.0) if trinitycore is not None else 0.0

    def _conf_edits(
        self, server_dir: Path, text: str
    ) -> tuple[tuple[_ConfEdit, ...], tuple[str, ...]]:
        """The folder settings a repair sets in the install's confs, and why any is kept (T169).

        The owner's decision (2026-09-28): an install made before its game's table
        stated `LogsDir` -- TBC's and Vanilla's, which keep upstream's `""` and
        write their logs into `bin/` inside the container -- is offered the bind
        AND the setting, by one Repair. Nothing re-applies a conf table to a
        finished install, so without the setting the new bind would stay empty.

        A setting is set only where all three hold: the conf is in the server
        folder, it still leaves the key at upstream's `""` (a value somebody chose
        is never overwritten; `composegen.folder_settings()` binds it instead, or
        says in `kept` why it cannot), and the file on disk does not already bind
        the folder on the service that reads that conf (`composegen.bound_folders()`)
        -- the setting rides with its bind, so a player who puts `""` back on an
        install that has the bind is not offered it again.

        Only that key's line changes, through the conf stage's own patcher
        (`conf.patch()`, byte-preserving everywhere else), and the result is read
        back the way the server reads it (`composegen.conf_setting()`): a later
        spelling the patcher does not rewrite -- an indented or lower-case one,
        which the server reads last -- would leave the edit without effect, so it
        is not made and `kept` says why.

        Raises:
            InstallerError: a conf that is there could not be read.
        """
        # Local, as `_rollback_ground()` imports `dockerfile`: `families` imports this module.
        from yulon.catalog.catalog import ConfPatch
        from yulon.catalog.families import conf

        native_block = self.entry.install.native
        # `composegen.built_here()`'s block, the one `folder_settings()` below reads
        # too: a TrinityCore install's confs carry the same `DataDir`/`LogsDir` keys
        # in the same kind of table (T179 Task 3), so the two cannot disagree about
        # which table a repair is about.
        built = None if native_block is None else composegen.built_here(native_block)
        if built is None:
            return (), ()
        texts: dict[str, str] = {}
        paths: dict[str, Path] = {}
        for name, patch in built.conf.files.items():
            if not any(key in patch.keys for key in composegen.SERVER_FOLDER_KEYS):
                continue
            path = server_dir / composegen.SERVER_CONF_DIR / name
            try:
                texts[name] = _read_exact(path)
            except FileNotFoundError:
                continue
            except (OSError, UnicodeDecodeError) as exc:
                raise InstallerError(
                    f"{path} could not be read ({exc}), so Yu'lon cannot tell which folder the "
                    "server writes into."
                ) from exc
            paths[name] = path
        try:
            settings = composegen.folder_settings(self.entry, texts)
        except composegen.ComposeGenError as exc:
            raise InstallerError(str(exc)) from exc
        kept = [setting.why for setting in settings if setting.why]
        edits: list[_ConfEdit] = []
        for name, before in texts.items():
            # The binds of the service that reads THIS conf (cold review): a
            # `./logs` on mangosd is not realmd's.
            bound = composegen.bound_folders(self.entry, text, name)
            after = before
            done: list[str] = []
            for setting in settings:
                folder = setting.folder
                if setting.conf != name or not setting.unset or folder is None:
                    continue
                if folder.spec in bound:
                    continue
                trial = conf.patch(after, ConfPatch(keys={setting.key: setting.stated}), {})
                if composegen.conf_setting(trial, setting.key) != setting.stated.strip('"'):
                    kept.append(
                        f"{composegen.SERVER_CONF_DIR}/{name} sets {setting.key} on more than "
                        "one line, and the server reads a later one Yu'lon would not change, so "
                        f"it is left as it is. {setting.key} = {setting.stated} as the last such "
                        "line keeps what the server writes there in the server folder."
                    )
                    continue
                after = trial
                done.append(
                    f"{setting.key} = {setting.stated} in {composegen.SERVER_CONF_DIR}/{name}"
                )
            if done:
                edits.append(_ConfEdit(paths[name], before, after, tuple(done)))
        return tuple(edits), tuple(kept)

    def _upstream_compose_facts(
        self, server_dir: Path, text: str
    ) -> tuple[ComposeCheck, str | None, str | None] | None:
        """`_base_compose_facts()` for the repository's own file in a server Yu'lon built (T170).

        None when `text` is not that file -- it is then `foreign`, as before.
        It is that file only where all three hold:

        * the checkout IS the server folder (`_checkout_is_the_server_dir()`), so
          the repository ships a `docker-compose.yml` there at all -- and git is
          asked nothing on a CMaNGOS folder, which has no checkout at its root;
        * git says it is tracked and unmodified: `_replaceable_compose()`, the
          very rule the install's `generate-compose` stage replaces it by. An
          edited file, or one git cannot be asked about, stays somebody's own;
        * the folder's record says Yu'lon built this game here. The file names
          no compose project, so the project is read off the record instead:
          an install id that is not this folder's is a folder moved or copied
          after it was made (`moved`), and no record at all is a checkout
          somebody else set up, whose volumes a Yu'lon project would not find;
        * and no container brought up from this folder runs under another
          compose project (`_projects_brought_up_here()`). The record and the
          path travel with the folder -- restored at the same path on another
          machine, or brought up once with the repository's own stack, the
          characters are under the project those containers carry (Codex,
          T170 round 2). With no containers at all, the record decides.

        **The SELinux label is read off the override**, Yu'lon's own file
        beside it, which the reset left alone (it is untracked): `bind_label_of()`
        for the reason `_base_compose_facts()` gives. The host is asked only
        when that file is not Yu'lon's or has no host bind.
        """
        if not self._checkout_is_the_server_dir() or not self._replaceable_compose(server_dir):
            return None
        record = read_state(server_dir, valid=())
        if record is None or record.game_id != self.entry.id:
            return None
        path = server_dir / composegen.BASE_FILE
        if record.install_id != self._install_id(server_dir):
            return (
                ComposeCheck(
                    "moved",
                    f"{path} is the one that came with the repository, and this folder's "
                    f"{STATE_FILE} was written for another folder: the install was moved or "
                    "copied here after it was made. A file written for this folder would start "
                    "the server as a new project, away from its characters' database volume, so "
                    "nothing was written.",
                ),
                None,
                text,
            )
        owners = self._projects_brought_up_here(server_dir, path, text)
        if owners is not None:
            return owners
        override = server_dir / composegen.OVERRIDE_FILE
        label: str | None = None
        if override.is_file() and composegen.is_ours(override):
            try:
                label = composegen.bind_label_of(override.read_text(encoding="utf-8"))
            except composegen.MixedBindLabels as exc:
                return (
                    ComposeCheck(
                        "mixed",
                        f"{override}: {exc}, so Yu'lon cannot tell which way this install was "
                        f"made and does not write {path}. Nothing was written.",
                    ),
                    None,
                    text,
                )
            except (OSError, UnicodeDecodeError) as exc:
                return (
                    ComposeCheck(
                        "error", f"{override} could not be read ({exc}). Nothing was written."
                    ),
                    None,
                    text,
                )
        if label is None:
            label = self._bind_label(server_dir)
        try:
            fresh = self._render_compose(server_dir, self.resolve_secrets(server_dir), label).base
        except (composegen.ComposeGenError, InstallerError) as exc:
            return (
                ComposeCheck(
                    "error",
                    f"Yu'lon could not work out what {composegen.BASE_FILE} should say for this "
                    f"install: {exc} Nothing was written.",
                ),
                None,
                text,
            )
        added, removed = _lines_changed(text, fresh)
        return ComposeCheck("upstream", added=added, removed=removed), fresh, text

    def _projects_brought_up_here(
        self, server_dir: Path, path: Path, text: str
    ) -> tuple[ComposeCheck, str | None, str | None] | None:
        """Refuse the `upstream` repair when this folder's containers are another project's (T170).

        Asked of the containers' compose labels (`Seams.folder_projects`), not
        of their names: the repository's own compose file may name them
        anything. None when every container brought up from this folder is
        Yu'lon's project, or there are none -- then the record has decided.
        Only ever asked by the off-thread reading and the press after it.
        """
        ours = composegen.project_name(
            self.entry.id,
            server_dir,
            platform_id=self._seams.platform_id,
            install_id=self._install_id(server_dir),
        )
        folders = dict.fromkeys((server_dir, server_dir.resolve()))
        found: list[str] = []
        for folder in folders:
            projects = self._seams.folder_projects(folder)
            if projects is None:
                return (
                    ComposeCheck(
                        "error",
                        f"Docker would not say which containers were brought up from {server_dir}, "
                        "so Yu'lon cannot tell whether this server's characters are under its own "
                        f"compose project, and does not write {path}. Nothing was written. Check "
                        "the docker daemon is up, then press Refresh.",
                    ),
                    None,
                    text,
                )
            found.extend(projects)
        others = sorted({project for project in found if project != ours})
        if not others:
            return None
        return (
            ComposeCheck(
                "foreign",
                f"{path} is the one that came with the repository, and the containers brought up "
                f"from this folder run as the compose project {', '.join(others)}, not {ours}, "
                "the one Yu'lon writes for it. This server's characters are under "
                f"{others[0]}: a file of Yu'lon's would start the server as {ours}, beside an "
                "empty database volume, so nothing was written. Start this server the way those "
                "containers were made.",
            ),
            None,
            text,
        )

    def base_compose_check(self, options: InstallOptions | None = None) -> ComposeCheck:
        """Is this install's `docker-compose.yml` what this version of Yu'lon writes? (T106)

        A reading: it never raises and never writes. See `ComposeCheck` for the states.
        """
        check, _fresh, _text, _edits = self._base_compose_facts(
            self.server_dir(options or InstallOptions())
        )
        return check

    def repair_base_compose(
        self, options: InstallOptions | None = None, *, now: datetime | None = None
    ) -> ComposeRepaired:
        """Re-render this install's `docker-compose.yml` from the current template (T106).

        The press behind "Repair server files…". The owner's decision (2026-09-24):
        the player chooses when, the file is backed up first, and nothing else is
        touched -- not the override, not the build file, not `.env`, not a
        container. The running containers keep the file they were created from
        until they are recreated, which the Server tab then offers. The one
        addition (T165) is a folder the new file binds and the folder lacks,
        made empty first by `_make_server_folders()` so Docker does not make it
        as root; one that is there is left as it is. The other (T169) is the one
        conf line that makes the server write into such a folder, where the conf
        still leaves it at upstream's `""` (`_conf_edits()`): backed up and
        written the same way, after the compose file's own backup is taken and
        checked (so a backup that fails has truly written nothing) and BEFORE the
        compose file is replaced, so a press stopped between the two leaves a
        compose file still `stale` and the next press finishes -- the other way
        round, a file already binding the folder would read `current` and the
        conf it needs would never be offered again. A refusal after a conf was
        set names it and its backup, and says to press Repair again first.

        Asked again here rather than trusting the tab's earlier reading, because the
        folder can change between the two. Writes only on `stale` and, since T170,
        `upstream` -- the repository's own file a failed Update left in a WotLK
        folder, replaced by the install's own rule and backed up the same way:
        `current` writes nothing and says so with `backup=None`; every other state
        refuses.

        The write: a stamped `.repair.bak` copy first (`copy2`, so it keeps the old
        mode and mtime), checked to hold exactly the text that was validated; then
        `_replace_if_unchanged()` -- a unique fsynced temp file with the target's
        mode, `os.replace`d onto the target only if the target still says what was
        checked (Codex, 2026-09-24: the check-to-write race). Atomic on POSIX and
        Windows, so a full disk leaves the old file whole and the backup beside it.

        Raises:
            InstallerError: the check's refusal, a render or read that failed, or a
                backup or write that failed. Each says that nothing was written, or
                that the old file is still in place.
        """
        server_dir = self.server_dir(options or InstallOptions())
        path = server_dir / composegen.BASE_FILE
        check, fresh, text, edits = self._base_compose_facts(server_dir)
        if check.state == "current":
            return ComposeRepaired(path, None)
        if check.state not in ("stale", "upstream") or fresh is None or text is None:
            raise InstallerError(check.why)
        changed = (
            f"{path} changed while it was being repaired, so it was not replaced and is "
            "left as it is now. Check again (Refresh) and press Repair once more."
        )
        when = now or datetime.now()
        # The compose file's backup first, and checked, BEFORE any folder is made
        # or any conf edited (Codex, T169 rounds 2-3): a backup that fails then
        # truly leaves nothing written, and `_backup_beside()` removes a copy
        # it could not finish.
        try:
            backup = _backup_beside(path, when)
        except OSError as exc:
            raise InstallerError(
                f"{path} could not be backed up ({exc}), so it was not repaired. "
                "Nothing was written."
            ) from exc
        if backup.read_text(encoding="utf-8") != text:
            # The copy is not of the file that was checked: it changed in between.
            backup.unlink(missing_ok=True)
            raise InstallerError(changed)
        # T165: the folders the new file binds, made before it can name them.
        # Only on an offered state, so a refused repair makes nothing either;
        # the check's `fresh` was rendered from the same table, so these are its
        # binds. A
        # folder that cannot be made takes the backup just made with it: nothing
        # was changed, so it would record nothing.
        try:
            self._make_server_folders(server_dir)
        except InstallerError as exc:
            backup.unlink(missing_ok=True)
            raise InstallerError(f"{exc} {path} is as it was, and no backup was made.") from exc
        done: list[tuple[Path, Path]] = []
        for edit in edits:
            done.append((edit.path, self._set_conf(edit, when, path, backup, done)))
        try:
            _replace_if_unchanged(path, fresh, text)
        except ComposeChangedError as exc:
            raise InstallerError(
                f"{changed} Its backup from before is {backup.name}.{_confs_already(done)}"
            ) from exc
        except OSError as exc:
            raise InstallerError(
                f"{path} could not be written ({exc}). The old file is still in place, and its "
                f"backup is {backup.name}.{_confs_already(done)}"
            ) from exc
        logger.info(f"repaired {path}; the old file is {backup.name}")
        return ComposeRepaired(path, backup, tuple(conf_backup for _conf, conf_backup in done))

    @staticmethod
    def _set_conf(
        edit: _ConfEdit,
        when: datetime,
        compose: Path,
        compose_backup: Path,
        done: Sequence[tuple[Path, Path]],
    ) -> Path:
        """Write one conf edit of a repair (T169), backed up first; return the backup.

        The compose file's rule, byte for byte: a stamped `.repair.bak` beside it
        (`copy2`, so the owner-only mode of a file holding the database password
        is kept), checked to hold exactly what was read, then
        `_replace_if_unchanged(exact=True)`. `done` are the confs this press
        already set, with their backups, which a refusal names: they stay set,
        and the compose file is still `stale`, so pressing Repair again finishes
        the rest. `compose_backup` is the copy already taken of the compose file.

        Raises:
            InstallerError: the backup or the write failed, or the conf changed
                since it was read; the conf is as it was.
        """
        path = edit.path
        untouched = (
            f"{compose.name} was not repaired (a copy of it was already taken, "
            f"{compose_backup.name}).{_confs_already(done)}"
        )
        try:
            backup = _backup_beside(path, when)
        except OSError as exc:
            raise InstallerError(
                f"{path} could not be backed up ({exc}), so it was not changed and {untouched}"
            ) from exc
        changed = (
            f"{path} changed while it was being repaired, so it was not changed and "
            f"{untouched} Check again (Refresh) and press Repair once more."
        )
        if _read_exact(backup) != edit.before:
            backup.unlink(missing_ok=True)
            raise InstallerError(changed)
        try:
            _replace_if_unchanged(path, edit.after, edit.before, exact=True)
        except ComposeChangedError as exc:
            raise InstallerError(f"{changed} Its backup from before is {backup.name}.") from exc
        except OSError as exc:
            raise InstallerError(
                f"{path} could not be written ({exc}), so it is as it was and {untouched} Its "
                f"backup is {backup.name}."
            ) from exc
        logger.info(f"set {', '.join(edit.settings)}; the old file is {backup.name}")
        return backup

    def built_image_refs(self, ctx: StageContext) -> tuple[str, ...]:
        """The image references this install's build produces, fully qualified.

        Split out of `built_images()` on 2026-09-05 so that a refusal telling a
        user to REMOVE those images names the same strings the skip decision was
        made of. Two callers each spelling `composegen.built_image_refs(...)`
        would be two chances to name an image this install does not have, and a
        `docker image rm` on the wrong tag either does nothing or removes
        somebody else's build -- neither of which says which happened.
        """
        return self.image_refs_at(ctx.server_dir)

    def image_refs_at(self, server_dir: Path) -> tuple[str, ...]:
        """`built_image_refs()` for a caller with a folder and no stage context.

        The ONE spelling of `composegen.built_image_refs(...)` in this class.
        Preflight asks it before any stage context exists (T112, `_spent()`), and
        lowering the free-space floor on a tag the build stage would not check
        is the two-spellings defect above, pointed at a disk.
        """
        return composegen.built_image_refs(
            self.entry,
            server_dir,
            platform_id=self._seams.platform_id,
            install_id=self._install_id(server_dir),
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
        # T203: what the cache holds as this build starts, so the next press's
        # preflight can credit only what THIS build added to it -- or, after an
        # attempt that did not finish, what that one added (`BUILD_CACHE_FILE`).
        yield BUILD_CACHE_ASKING
        baseline = build_cache_baseline_for(ctx.server_dir, self._seams.build_cache_bytes())
        write_build_cache_baseline(ctx.server_dir, baseline)
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
        # T223 (owner D3): a build that could not reach Docker Hub to look up its
        # base image compiled nothing, so it is tried once more after a pause, and
        # a second such failure says what happened instead of "the build failed".
        for second_try in (False, True):
            # Until the run answers, it is one that may have tagged (scoped re-review
            # N1): a consumer that walks away, or a Ctrl+C, leaves `_pump()` before any
            # return code, with compose possibly past its export.
            self._build_exit = docker.CANCELLED_RETURNCODE
            try:
                run = yield from self._pump(
                    lambda sink: self._seams.build(
                        ctx.server_dir, composegen.COMPOSE_FILES, sink=sink, cancel=ctx.cancel
                    ),
                    cancel=ctx.cancel,
                    stage="build",
                    watch=QuietWatch(
                        notice_after=BUILD_QUIET_NOTICE_SECONDS,
                        stalled_after=BUILD_STALLED_SECONDS,
                        notice=build_quiet_notice(),
                        stalled=build_stalled_notice(),
                    ),
                )
            except InstallerError:
                # The command could not be run at all: nothing of it can land later.
                self._build_exit = None
                raise
            self._build_exit = run.returncode
            unreachable = (
                docker.base_image_unreachable(run.tail)
                if run.returncode not in (0, docker.CANCELLED_RETURNCODE)
                else ""
            )
            if not unreachable or second_try:
                break
            yield (
                f"Docker Hub could not be reached to look up the base image {unreachable}, so "
                f"nothing was compiled. Trying the build again in {_spell_seconds(HUB_RETRY_S)}."
            )
            self._pause(HUB_RETRY_S, ctx.cancel)
            if ctx.cancel is not None and ctx.cancel.is_set():
                raise InstallStopped(_cancelled_message("the build"))
        if unreachable:
            again = (
                f"press the same entry under \u201c{server_build_presses.SERVER_BUILD}\u201d on "
                f"the Modules tab again"
                if ctx.force_build
                else "try again"
            )
            raise InstallerError(
                f"Docker Hub could not be reached to look up the base image {unreachable}, twice, "
                f"{_spell_seconds(HUB_RETRY_S)} apart, so nothing was compiled. Check this "
                f"computer's internet connection, then {again}."
            )
        self._check_run(run, "the build", ctx.cancel, build_cancel_note(), from_build=True)
        write_build_cache_baseline(ctx.server_dir, baseline, finished=True)
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
            self._seams.start_db(
                self.entry.container_spec(), ctx.server_dir, because="nothing was imported"
            )
        except docker.DockerCommandError as exc:
            # T248: Yu'lon's own sentence already ends "so nothing was imported";
            # Docker's own words get the engine's opening in front of them.
            said = (
                str(exc)
                if isinstance(exc, SaidByYulon)
                else f"The database could not be started, so nothing was imported: {exc}"
            )
            raise carry_detail(exc, InstallerError(said)) from exc
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
        if service is not None:
            # T539: never read, clear or import under an importer still running from
            # an earlier run -- a Stop whose importer outlived it, or a Yu'lon that
            # closed mid-import. One that can be ended is ended first, and BEFORE the
            # probe (cold review): a live importer changes what the probe would read.
            left = self._seams.end_one_shot(service, ctx.server_dir)
            if left is not None:
                raise OneShotLeftRunning(_one_shot_left_sentence(left, earlier=True))
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
        # T121: HERE, and only here -- the old databases are gone (`absent`, or
        # `partial` with `reset()` having dropped them just above), so no mob
        # multiplier is applied to what is about to be imported. Not before the
        # reset (Codex final pass): a reset that raises or drops nothing leaves
        # the old schemas, and the record describing them must stay.
        yield from self._forget_old_database_records(ctx)
        if service is None:
            return
        yield f"Importing the databases ({service}). This takes several minutes."
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(
                service, ctx.server_dir, sink=sink, cancel=ctx.cancel
            ),
            cancel=ctx.cancel,
            stage="import",
        )
        if run.returncode == docker.CANCELLED_RETURNCODE:
            # T539: the note promises the half-written databases are cleared before the
            # import runs again, which is true only once nothing is still writing them.
            left = self._seams.end_one_shot(service, ctx.server_dir)
            if left is not None:
                raise OneShotLeftRunning(_one_shot_left_sentence(left, earlier=False))
            raise InstallStopped(_cancelled_message("the database import", IMPORT_CANCEL_NOTE))
        try:
            after = self._seams.verify_import(gate.probe, service, ctx.server_dir, run)
        except docker.DockerCommandError as exc:
            raise carry_detail(exc, InstallerError(str(exc))) from exc
        yield f"The databases now read as {after.state}."

    def _forget_old_database_records(self, ctx: StageContext) -> Iterator[str]:
        """Drop the answers file's `applied`/`pending` maps once the old databases are gone (T121).

        The file is one of `OUR_OWN_FILES`, so it outlives the databases it
        describes: left there, it read Baby Mobs as installed on stock creatures,
        and the Remove it offered would divide them. The saved answers stay.

        A clear that cannot be written FAILS the stage (Codex final pass): it was
        a warning, and the import went on to leave a readable stale record. The
        stage is not recorded, so the next Install press tries again -- and finds
        the databases `absent`, which is this same branch.
        """
        forgot, problem = module_answers.forget_database_records(ctx.server_dir)
        if problem:
            raise InstallerError(
                f"Yu'lon could not update {module_answers.ANSWERS_FILE} in {ctx.server_dir} "
                f"({problem}). That file still says which mods were applied to the "
                "old databases, and these are new, so nothing was imported. Fix its permissions "
                "(or, if it is damaged, move it aside) and press Install again."
            )
        if forgot:
            yield (
                "Cleared Yu'lon's record of the mods applied to the old databases: "
                "these are new."
            )

    def stage_up(self, ctx: StageContext) -> Iterator[str]:
        """Start the three long-running services, and only those.

        Never recorded: a resume has to end with the server actually running,
        and this is cheap. `start_staged()` names the services explicitly so
        `up` can never select the one-shot import — the thing `dml-start.sh`
        warns about in as many words.
        """
        refused = self.start_refusal(ctx.server_dir)
        if refused is not None:
            raise InstallerError(f"{refused} The server was not started.")
        yield "Starting the server."
        warned = self._put_back_the_zone_file(ctx.server_dir)
        if warned is not None:
            yield warned
        warned = self._refresh_world_data(ctx.server_dir)
        if warned is not None:
            yield warned
        # T577: a core whose world never marks its realm offline while it loads is marked
        # here, before the world exists to be logged in to. Best effort, never raising.
        self._seams.mark_realm_offline(
            self.entry, self.entry.container_spec(), ctx.server_dir, unless_world_up=True
        )
        try:
            self._seams.start(self.entry.container_spec(), ctx.server_dir)
        except docker.DockerCommandError as exc:
            raise carry_detail(exc, InstallerError(f"The server would not start: {exc}")) from exc

    def stage_ready(self, ctx: StageContext, *, stop_lets_it_load: bool = False) -> Iterator[str]:
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
            kept = yield from self._last_lines(ctx, (spec.db,), ctx.cancel)
            raise InstallerError(
                f"The database never reported healthy. Its own log says why.{kept}",
                detail=docker.logs_command(spec.service_for(spec.db), ctx.server_dir),
            )
        yield from self.wait_for_ready(
            ctx, self._native().ready, stop_lets_it_load=stop_lets_it_load
        )

    def _last_lines(
        self,
        ctx: StageContext,
        containers: Sequence[str],
        cancel: threading.Event | None,
    ) -> Generator[str, None, str]:
        """Show each container's last lines before a failure is raised (T249).

        A failed install is never remembered, so **Save logs for support…** has
        no install to read a container for, and the world server's own log was
        the one thing the reported crash loop needed. What the job yields is on
        the screen, in the CLI's transcript and in the run log the support file
        zips, so the lines go there, each as a program's output under its
        container's name (`lines.TOOL`): a server line spelled like `Step 3 of
        9` or `--- ` is never read as the engine's own.

        The install's database password is masked here, where the lines are
        made: the screen and the transcript are not redacted on the way out.

        `cancel` is what ends the reads. The ready wait passes the job's Stop,
        or, on a press that lets a loading world finish (T158) once a Stop has
        come, "Stop now anyway", so a load that then crashes still shows its
        lines.

        Returns the sentence the failure appends, naming where the lines are, or
        `""` when none could be read and so none were shown.
        """
        redact = Redactor.build([ctx.secrets.db_password]).redact
        shown = False
        for container in dict.fromkeys(containers):
            read = _unless_stopped(self._seams.container_tail, container, cancel)
            if read is _STOPPED:
                yield f"Stopped before {container}'s log was read."
                break
            text = read if isinstance(read, str) else None
            if text is None:
                yield f"{container}'s log could not be read."
                continue
            said = _kept_end(text, FAILURE_TAIL_BYTES).splitlines()
            if not said:
                yield f"{container} has printed nothing."
                continue
            yield f"The last lines {container} printed:"
            for line in said:
                yield lines.TOOL + f"{container} | {redact(line)}"
            shown = True
        if not shown:
            return ""
        return (
            " Its last lines are in the log above, and Logs → Save logs for support… puts "
            "them in a file you can send to whoever is helping you."
        )

    def wait_for_ready(
        self, ctx: StageContext, markers: ReadyMarkers, *, stop_lets_it_load: bool = False
    ) -> Iterator[str]:
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

        **The banner is not the end of the wait** (T71). `docker.wait_ready()`
        answers True on the first match and used to end this generator with
        "The server is up."; `watch_after_ready()` then keeps looking for
        `READY_GRACE_SECONDS`, and a world that restarts, stops or prints its
        family's `fatal` line in that minute ends this stage in a failure that
        quotes what it said. Its constant holds the measurement.

        That failure is a `WorldStoppedAfterReadyError` and the others are not,
        which is the owner's answer of 2026-09-16 about what a REBUILD does with
        it: this one keeps the new build (the compile was fine; the server it
        made started), every pre-banner verdict below still rolls the images
        back. `rebuild()` is the only reader of the distinction.

        Two gaps inherited from `docker.wait_ready()`, recorded in 7.1 and still
        true: its crash-loop latch and its `fatal` search both look at the WORLD
        container only, so an auth container that loops or prints a fatal line
        is not seen. Both are conservative — slower to give up, never quicker to
        call a dead server ready.

        **Stop is heard within one poll (T247)**: `ctx.cancel` rides to the
        real wait on `docker.ReadySpec.cancel` and to the watch after the
        banner. A window the Stop cut short is never read as silence, but a
        verdict that is a failure in its own right -- a crash loop, a container
        gone, a fatal line, a world that stopped after its banner -- is still
        that failure: the Stop did not cause it. What the Stop then does:

        * plain (an install, the finish of a world update): `ReadyWaitStopped`
          at once, and the world is left loading;
        * `stop_lets_it_load` (a rebuild, which Update and Return run through;
          the lead's ruling of 2026-10-05): T158's rule, never kill a loading
          world. The Stop is said (`docker.STOP_WAITS_FOR_THE_LOAD`) and the
          wait goes on until the world is ready, crashes or runs out; ready and
          watched, it still raises (`READY_STOPPED_AFTER_LOADING`), because the
          player asked to stop; crashed in the watch, it raises
          `CrashedAfterStop`, which rolls back where T71 would keep the build.
          "Stop now anyway" ends it early (`READY_WAIT_STOPPED_ANYWAY`);
        * inside the watch after the banner, before its minute is over:
          `READY_STOPPED_IN_THE_WATCH`. In its last pause: the build met its
          proof first, so it is kept (`READY_STOP_TOO_LATE`).

        A caller that must not be stopped (the rollback's wait for the build it
        put back) passes a context with no cancel.
        """
        spec = self.entry.container_spec()
        # What ends a window now: the job's Stop -- or, once a Stop has been heard
        # and the load is being let finish (`stop_lets_it_load`), the panel's
        # "Stop now anyway", which rides on the Cancel (`docker.CancelWithForce`).
        force = ctx.cancel.anyway if isinstance(ctx.cancel, docker.CancelWithForce) else None
        ends: threading.Event | None = ctx.cancel
        pending = False
        ready = replace(self._ready_spec(markers), cancel=ends)

        def reads_end() -> threading.Event | None:
            # What ends a failure's last-line reads (T249). On a press that lets
            # the load finish, a Stop means "let it load" whether or not it was
            # heard before the crash was seen: only "Stop now anyway" ends them.
            if stop_lets_it_load and not pending and ends is not None and ends.is_set():
                return force
            return ends

        service, container = spec.service_for(spec.world), spec.world
        # T248: the line says whose log in words; the command is under Details.
        logs = f"{container}'s own log"
        servers = (spec.world, spec.auth)
        read_it = docker.logs_command(service, ctx.server_dir)
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
                yield (
                    f"The world server reported ready; watching it for "
                    f"{_spell_seconds(READY_GRACE_SECONDS)} to be sure it stays up."
                )
                after = watch_after_ready(
                    lambda: self._seams.world_output(spec),
                    self._seams.monotonic,
                    self._seams.sleep,
                    interval=ready.interval,
                    banner=ready.world,
                    fatal=ready.fatal,
                    cancel=ends,
                )
                if after.cut_short and pending:
                    # Stop came during the LOAD; only "Stop now anyway" came in the watch.
                    raise StoppedInTheWatch(READY_ANYWAY_IN_THE_WATCH)
                if after.cut_short:
                    raise StoppedInTheWatch(READY_STOPPED_IN_THE_WATCH)
                if not after.stopped and pending:
                    # The lead's ruling: the load was let finish, and it succeeded,
                    # but the player asked to stop -- so the build from before goes
                    # back. The world has loaded, so its stop is a clean one.
                    yield docker.WORLD_FINISHED_LOADING
                    raise ReadyWaitStopped(READY_STOPPED_AFTER_LOADING)
                if not after.stopped:
                    if ctx.cancel is not None and ctx.cancel.is_set():
                        # The lead's ruling: too late means the press SUCCEEDED. The
                        # Stop is taken back, so the rest of the press runs and the
                        # panel says it finished (`withdraw_stop()`).
                        withdraw_stop(ctx.cancel)
                        yield READY_STOP_TOO_LATE
                    yield "The server is up."
                    return
                said = (
                    f" Its last words were:\n{ansi.strip(after.words)}"
                    if after.words
                    # NOT "it printed nothing": this watch may have seen the
                    # container only after docker had already restarted it, in
                    # which case what that run said is not in this run's output
                    # any more — but it IS in the command above, which prints
                    # every run the container has had.
                    else " What it said as it went is in the log of the run before this one."
                )
                kept = yield from self._last_lines(ctx, servers, reads_end())
                if pending:
                    # The lead's ruling (2026-10-05): the player asked to stop and
                    # the build then crashed -- both point back, so this is a crashed
                    # wait's rollback, not T71's keep.
                    raise CrashedAfterStop(
                        f"{READY_CRASHED_AFTER_STOP} {logs} has the rest.{kept}{said}"
                    )
                raise WorldStoppedAfterReadyError(
                    f"The world server came up and then stopped. {container} printed its "
                    f"ready marker and was gone again inside "
                    f"{_spell_seconds(READY_GRACE_SECONDS)}, so the server is not running "
                    f"even though it started. {logs} has the rest.{kept}"
                    f"{_missing_table_hint(after.words, self.entry)}{said}",
                    detail=read_it,
                )
            now = self._seams.world_output(spec)
            first_restarts = _restart_baseline(first_restarts, now)
            verdict, detail = _read_world(
                before, now, first_restarts, markers.restart_loop, ready.fatal
            )
            spent = self._seams.monotonic() - started
            if verdict in ("alive", "quiet", "unreadable") and ends is not None and ends.is_set():
                # Asked here and not before the verdict: these three are about the
                # window, which the Stop cut short; a loop, a container gone or a
                # fatal line are about the server, which it did not touch.
                if pending:
                    raise ReadyWaitStopped(READY_WAIT_STOPPED_ANYWAY)
                if not stop_lets_it_load:
                    raise ReadyWaitStopped(READY_WAIT_STOPPED)
                # T158 reaches the ready wait (the lead's ruling, 2026-10-05): a new
                # world still loading may be in the middle of its database update,
                # so the Stop is heard and said now, and acted on once the load has
                # ended -- ready, crashed or out of time. Only "Stop now anyway"
                # ends this wait early.
                pending = True
                ends = force
                ready = replace(ready, cancel=ends)
                yield docker.STOP_WAITS_FOR_THE_LOAD
                if force is not None:
                    yield READY_WAIT_STOP_HINT
                before = now
                continue
            # Not for "unreadable": docker is not answering, and each ask would
            # cost its whole bound to get nothing. Nor for "alive": nothing ended.
            kept = ""
            if verdict in ("loop", "gone", "fatal", "quiet"):
                kept = yield from self._last_lines(ctx, servers, reads_end())
            if verdict == "loop":
                raise InstallerError(
                    f"{never_ready}: {container} restarted {detail} times while this waited, "
                    f"which is a crash loop and not a slow start. {logs} has what it printed "
                    f"before each one.{kept}{_corrections_hint(self.entry, now.text)}",
                    detail=read_it,
                )
            if verdict == "gone":
                raise InstallerError(
                    f"{never_ready}: {container} is not running any more (docker says "
                    f"{detail!r}), so nothing is going to print it. {logs} has its "
                    f"last words.{kept}",
                    detail=read_it,
                )
            if verdict == "fatal":
                raise InstallerError(
                    f"{never_ready}. It printed a line that means it never will: "
                    f"{detail!r}. {logs} has the rest.{kept}"
                    f"{_corrections_hint(self.entry, now.text)}",
                    detail=read_it,
                )
            if verdict == "quiet":
                silent_for = self._seams.monotonic() - window_started
                raise InstallerError(
                    f"{never_ready}, and it stopped printing anything at all for the last "
                    f"{_spell_seconds(silent_for)} — a server that is still loading says so as "
                    f"it goes, so this one is stuck rather than slow. {logs} has its last words."
                    f"{kept}",
                    detail=read_it,
                )
            if verdict == "unreadable":
                blind_for = self._seams.monotonic() - window_started
                raise InstallerError(
                    f"The wait for {container} stopped because docker stopped answering: "
                    f"for the last {_spell_seconds(blind_for)} neither its state nor its log "
                    f"could be read, so nothing here knows whether the server is still "
                    f"loading, finished, or gone. The install itself got as far as starting "
                    f"the containers. Check the docker daemon is up, then read {logs}.",
                    detail=read_it,
                )
            before = now
            yield (f"Still loading after {_spell_seconds(spent)}, and still printing — waiting on.")
        lasted = self._seams.monotonic() - started
        kept = yield from self._last_lines(ctx, servers, reads_end())
        raise InstallerError(
            f"{never_ready}. It was still printing after "
            f"{_spell_seconds(lasted)}, so it is doing something "
            f"without finishing it. This wait gives a server that keeps talking another "
            f"{_spell_seconds(quiet)} every time it prints, up to a ceiling of "
            f"{_spell_seconds(READY_CEILING_SECONDS)}, which is many times the slowest first "
            f"boot this has been measured against. {logs} has what it is doing.{kept}",
            detail=read_it,
        )

    def _ready_spec(self, markers: ReadyMarkers) -> docker.ReadySpec:
        """`ready_spec_for()` for this installer's entry."""
        return ready_spec_for(self.entry, markers)

    # -- what the realm advertises, once everything else has finished ----

    def _advertise_realm(self, ctx: StageContext) -> Iterator[str]:
        """Set the realm's advertised address to one other machines can reach.

        The fix for bug-checklist §35, driven on real hardware 2026-09-02: a
        WoW TBC server this app installed on m910q was joined from another PC
        over Tailscale, auth succeeded, the realm list arrived, and the world
        connect could not — `realmd.realmlist.address` was still
        `127.0.0.1`, so the client had been told the world server was on its
        OWN machine and hung at "Connecting" saying nothing. One
        `UPDATE realmd.realmlist SET address='100.64.0.10' WHERE id=1` later
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
        (`.notes/gates/bug41-loopback-2026-09-05/mutations-round3.txt`). Reading
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

    def _clone_lines(self, spec: git.CloneSpec, stage: str) -> Iterator[str]:
        """Clone through the seam, relaying whatever git says while it works (T35).

        A generator rather than the plain call it replaced, because the clone is
        the first stage of an install that takes minutes on a large repository
        and said one sentence for the whole of it. `git.clone_lines()` decides
        whether the seam behind this can talk — a plain function cannot, and
        every test's clone double is one, so those runs are exactly as silent as
        they were.

        `stage` is this stage's own name, which the body cannot know: it is
        bound by the FAMILY in its `Stage` tuple, and it rides into the progress
        line so the header strip attributes the reading to the stage the user
        can see in `--- <name>`.
        """
        try:
            yield from git.clone_lines(self._seams.clone, spec, stage=stage)
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
        stage: str,
        watch: QuietWatch | None = None,
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

        **Everything that comes through here is a subprocess talking**, which is
        what makes this the place to mark it (T35). The panel dims tool output
        and moves its strip on a number in it, and the engine's OWN sentences —
        which the stage bodies `yield` directly, never through this queue — stay
        unmarked. Marked on the sink rather than on the way out for the reason
        `cmangos._stream()` must: that bridge carries both kinds on one queue,
        and the two have to be distinguishable somewhere.

        `stage` names the activity for the progress line's field; see
        `lines.relayed()`.

        `watch` is the build's silence watch (T202): after `notice_after`
        seconds with no line its notice is said, after `stalled_after` its
        stalled notice, each once per silence. It ends nothing and sets
        nothing -- see `BUILD_STALLED_SECONDS` for the ruling and why.
        """
        queued: queue.Queue[str | None] = queue.Queue()
        outcome: list[docker.AttachedRun] = []
        failure: list[BaseException] = []

        def work() -> None:
            try:
                ran = call(lambda line: _put_all(queued, line, stage))
                # Read the moment the command returned (T250 review): `_check_run()`
                # counts a bare exit 1 as the Stop only if the Stop came first.
                stopped = cancel is not None and cancel.is_set()
                outcome.append(replace(ran, stop_seen=stopped) if stopped else ran)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
                failure.append(exc)
            finally:
                queued.put(None)

        worker = threading.Thread(target=work, daemon=True, name="yulon-install-output")
        worker.start()
        try:
            if watch is None:
                while True:
                    item = queued.get()
                    if item is None:
                        break
                    yield item
            else:
                yield from _watched(queued, watch)
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
        *,
        from_build: bool = False,
    ) -> None:
        """`note` is what a Stop costs FOR THIS STAGE, and only this stage.

        Required rather than defaulted, because there is no sentence about
        keeping work that is true of the build, the download and the import
        alike — and the copy that was true of one was being said for all three.
        A default here is the shape that mistake had.
        """
        if run.returncode == docker.CANCELLED_RETURNCODE or (
            cancel is not None and cancel.is_set() and _a_stops_exit(run)
        ):
            # Asked before the exit status (T250 review): a child another route
            # ended under a Stop exits with a code of its own, and "failed (exit
            # 143)" would be a refusal for a button the player pressed. Only an
            # exit a Stop makes, though: a compile error that lands as Stop is
            # pressed is still the compile error (`_a_stops_exit()`).
            raise InstallStopped(_cancelled_message(what, note))
        if run.returncode != 0 and from_build and docker.builder_connection_lost(run.tail):
            # T202: said in words before the quote, which alone told the
            # player nothing (a raw gRPC line, left-truncated).
            raise InstallerError(
                f"{what} failed (exit {run.returncode}) {BUILDER_LOST} Its last words were: "
                f"{docker.last_words(run.tail, from_build=from_build)}"
            )
        if run.returncode != 0:
            raise InstallerError(
                f"{what} failed (exit {run.returncode}). Its last words were: "
                f"{docker.last_words(run.tail, from_build=from_build)}"
            )
        self._check_cancel(cancel)

    def _check_cancel(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise InstallStopped(_cancelled_message("the install"))

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

    def _record_error(
        self, server_dir: Path, state: InstallState, message: str, *, run: str = ""
    ) -> None:
        """Record a failure and which kind of run it was (T207); a repair press by default.

        The default is the safe side of T206's question: only `run()`, the install,
        says `ERROR_RUN_INSTALL`, and only that kind lets the next install stop the
        world the failed run left.
        """
        if not (server_dir / STATE_FILE).is_file():
            return
        write_state(
            server_dir,
            replace(state, last_error=message, error_run=run or ERROR_RUN_REBUILD),
        )


STOP_SIGNAL_EXITS = frozenset({143, 137, -15, -9})
"""Exit codes a Stop makes on Linux and macOS: SIGTERM and SIGKILL, as a shell reports them
(128 + signal) and as Python reports a child it signalled (negative)."""

WINDOWS_ENDED_EXIT = 1
"""What a docker CLI ended by Stop exits with on Windows (TerminateProcess). A real failure
exits 1 too, so it counts as the Stop only if the Stop came before the command returned."""


def _a_stops_exit(run: docker.AttachedRun) -> bool:
    """Is this exit one a Stop makes, rather than the command's own failure (T250 review)?"""
    if run.returncode in STOP_SIGNAL_EXITS:
        return True
    return sys.platform == "win32" and run.returncode == WINDOWS_ENDED_EXIT and run.stop_seen


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


def _watched(queued: queue.Queue[str | None], watch: QuietWatch) -> Iterator[str]:
    """`_pump()`'s read loop with T202's silence watch: it says, and never ends anything.

    The clock restarts on every line, so only an unbroken silence counts: a
    build that prints once a minute for four hours is never told anything.
    """
    quiet_since = time.monotonic()
    said = 0  # 0: nothing yet this silence; 1: the notice; 2: the stalled notice too
    while True:
        marks = (watch.notice_after, watch.stalled_after)
        timeout = None if said >= 2 else max(0.0, quiet_since + marks[said] - time.monotonic())
        try:
            item = queued.get(timeout=timeout)
        except queue.Empty:
            quiet = time.monotonic() - quiet_since
            if said == 0 and quiet >= watch.notice_after:
                said = 1
                yield watch.notice
            elif said == 1 and quiet >= watch.stalled_after:
                said = 2
                logger.warning(f"the build has printed nothing for {quiet:.0f}s; told the player")
                yield watch.stalled
            continue
        if item is None:
            return
        quiet_since = time.monotonic()
        said = 0
        yield item


def _put_all(queued: queue.Queue[str | None], line: str, stage: str) -> None:
    """Push every record `lines.relayed()` makes of one relayed line onto the bridge.

    A named function and not a lambda because a relayed line is now one OR two
    records — the line itself, and the reading taken out of it — and a
    comprehension inside a lambda reads as a trick where this reads as what it
    is. `cmangos._stream()` has the same two lines for the same reason.
    """
    for record in lines.relayed(line, stage=stage):
        queued.put(record)


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


def _one_shot_left_sentence(left: docker.OneShotLeft, *, earlier: bool) -> str:
    """What a database importer that could not be ended means, and how to end it (T539)."""
    names = ", ".join(left.names)
    who = f"the database importer ({names})" if names else "the database importer"
    if earlier:
        said = (
            f"A database import from an earlier run may still be running, and {who} could not "
            f"be ended: {left.reason}. The half-written databases were not cleared and nothing "
            "was imported."
        )
    else:
        said = (
            f"The database import was stopped, but {who} could not be ended: {left.reason}. "
            "It may still be writing to the databases, and Install again will not clear them "
            "while it runs."
        )
    if not left.names:
        return said
    return f"{said} To end it, run this in a terminal:\ndocker rm -f {' '.join(left.names)}"


def download_left_sentence(left: docker.OneShotLeft, *, earlier: bool) -> str:
    """`_one_shot_left_sentence` for the server-data download (re-review of 7312223b)."""
    names = ", ".join(left.names)
    who = f"its container ({names})" if names else "its container"
    if earlier:
        said = (
            f"A server-data download from an earlier run may still be running, and {who} could "
            f"not be ended: {left.reason}. Nothing was downloaded."
        )
    else:
        said = (
            f"The server-data download was stopped, but {who} could not be ended: "
            f"{left.reason}. It may still be writing the server data, and Install again will "
            "not start another while it runs."
        )
    if not left.names:
        return said
    return f"{said} To end it, run this in a terminal:\ndocker rm -f {' '.join(left.names)}"


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
