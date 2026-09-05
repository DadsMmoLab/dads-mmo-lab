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
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from secrets import token_hex
from typing import ClassVar, Protocol

from yulon import docker, git, networking, platform, resources, runner
from yulon.catalog import composegen, preflight
from yulon.catalog.catalog import CatalogEntry, EmulatorSource, NativeInstall, ReadyMarkers
from yulon.catalog.installer import (
    DockerUnavailableError,
    InstallerError,
    InstallOptions,
    UnsupportedPlatformError,
    docker_unavailable,
    provision_lines,
    unsupported_platform_message,
)
from yulon.log import get_logger
from yulon.ownership import Ownership as Ownership

logger = get_logger(__name__)

STATE_FILE = ".yulon-install.json"
STATE_VERSION = 1

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
    if unknown:
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


@dataclass(frozen=True)
class CallableGate:
    """An `ImportGate` from the two callables the app already injects for AzerothCore.

    `reset_fn` may be None — an engine built without a reset seam — and then
    `reset()` is the refusal the old `_import()` made inline: a half-written
    database with no way to clear it is a stop, not a guess.
    """

    probe_fn: docker.ImportProbe
    reset_fn: docker.ResetUnfinished | None

    def probe(self) -> docker.ImportState:
        return self.probe_fn()

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
    start_db: Callable[[docker.ContainerSpec, Path], None] = docker.start_database
    start: Callable[[docker.ContainerSpec, Path], bool] = docker.start_staged
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
            not server_dir.is_dir() or not _listing(server_dir, ignoring=STATE_FILE)
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

        try:
            with self._held_awake() as note:
                if note:
                    yield note
                stages = self.stages()
                for number, stage in enumerate(stages, start=1):
                    self._check_cancel(cancel)
                    # WHERE THE USER IS, on its own line and never folded into
                    # the `--- <name>` marker. That marker is what gate scripts,
                    # log captures and the interrupted-import watchers match on
                    # (`^--- import`), and one run WAS missed on 2026-09-03 by a
                    # watcher that could not see the stage it was armed for. A
                    # format everything greps is not a place to add fields.
                    #
                    # The percentage is of STAGES BEHIND YOU, not of work done:
                    # the twelve are wildly unequal -- `conf` is seconds and
                    # `build` is an hour -- so this says "9 of 12 started",
                    # which is true, rather than implying three quarters of the
                    # time is gone, which it is not. A resumed install counts
                    # the same way, because the stages it skips are done.
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
            # `.git` before cloning, so the claim written twenty lines above is
            # gone by the time this runs and the failure sentence is dropped on
            # the floor. Pinned in
            # `test_the_clone_that_fills_the_server_dir_takes_the_ownership_record_with_it`;
            # closing it means changing the clone, not this line.
            self._record_error(server_dir, state, str(exc))
            raise
        # OUTSIDE the `try`, and after the last stage, on purpose. Outside,
        # because everything in there is a reason to fail the install and this
        # is not one — a realm row that could not be written is a sentence, not
        # a failed install (`_advertise_realm()` raises nothing at all). After,
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
        install_id = composegen.install_id(server_dir, platform_id=self._seams.platform_id)
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
            leftovers = _listing(server_dir, ignoring=STATE_FILE)
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
            raise InstallerError(
                f"A container called {name} already exists and belongs to another install "
                f"({owner}). Two servers cannot share that name. Remove the other install's "
                "containers from its own tab first, then try again."
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
                leftovers = _listing(dest, ignoring=STATE_FILE)
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

    def built_images(self, ctx: StageContext) -> bool | None:
        """Does the daemon hold every image this install's build produces?

        `None` is "the daemon would not say", which is not "no".
        """
        return self._seams.images_built(
            composegen.built_image_refs(
                self.entry, ctx.server_dir, platform_id=self._seams.platform_id
            )
        )

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
        """
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

        The `BUILD_CANCEL_NOTE` this stage used to yield is gone from the body:
        the spine says a stage's cancel note right after `--- <name>` (A4).
        """
        built = self.built_images(ctx)
        if ctx.state.has("build") and built:
            yield "The server is already built; skipping the compile."
            return
        if ctx.state.has("build") and built is None:
            yield "Docker would not say whether this install is built, so it is being rebuilt."
        yield "Building the server. This takes hours on a first install; the output below is live."
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
            filled = composegen.fill(text, tokens)
            pattern = filled if markers.regex else re.escape(filled)
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
        one. Four outcomes, one line each:

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
        the assertion is per-thread and `platform.keep_awake()` refuses the main
        thread outright, which is right for the app (the install runs on a
        worker) and wrong to abort a command-line run over. The engine says so
        in its output instead of promising something it did not get.

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


def _listing(folder: Path, *, ignoring: str | None = None) -> list[str]:
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
    try:
        return [item.name for item in folder.iterdir() if item.name != ignoring]
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
