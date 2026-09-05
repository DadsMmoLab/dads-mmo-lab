"""Install options, errors, the engine protocol and the one dispatch (roadmap 3.1 → 7.2).

Until Phase 7 this module drove one catalog entry's bash `install-*.sh` on a
pty and answered its prompts from a rule table. 7.2 deleted that lineage — the
scripts, the rule table, the responder, the bash probe — and every entry now
installs through a family engine on `native.StagedInstaller`. What is left is
deliberately small: what the user decided (`InstallOptions`), what can go
wrong (`InstallerError` and its subclasses), how a folder is recognised as
holding an install (`compose_file()`), what a view can drive (`InstallEngine`),
and `installer_for()`, the ONE place a catalog entry becomes an engine.
Nothing in this module runs a subprocess or prompts.

`platform_names()`/`unsupported_platform_message()` stay because the engine's
preflight words its off-platform refusal with them; `docker_unavailable()` and
`provision_lines()` stay because the engine reports provisioning with them, and
they live here rather than in `native.py` so that the sentence a user reads
about Docker has one author; `cancelled_install_message()` stays because
`catalog_view.py` shows it after Stop, and `generated_compose_files()` beside
it because that copy's "Use existing…" half turns on which compose files the
engine itself wrote — a different question from `compose_file()`'s, and one
this module was answering with `compose_file()` until 2026-09-05. The copy asks
BOTH: `compose_file()` is what `attach_existing()` gates on, so it is what
decides whether the folder can be adopted at all, and a message that told the
user to delete a folder the app would adopt was the cost of asking only the
other one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from yulon import docker, platform, resources, runner
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

# Where the families' compose templates resolve from: `catalog/installers/<templates>/`.
DEFAULT_INSTALLERS_ROOT = resources.installers_dir()

# Every filename Docker Compose accepts for a project's compose file, in its own
# precedence order.
#
# Not ours to shorten. The app only ever looked for `docker-compose.yml`, which
# was what the WotLK and Tortoise scripts wrote - while the TBC and Vanilla ones
# wrote `compose.yml`. A finished install of those two was therefore invisible
# to "Use existing..." AND was thrown away by the remember check at the end of a
# multi-hour install, both reporting that nothing was installed.
#
# 7.2 deleted those scripts. Every family now renders its compose through
# `composegen`, whose `BASE_FILE` is `docker-compose.yml`, so nothing Yu'lon
# writes today is called `compose.yml`. The list stays anyway, and not out of
# caution: installs made before 7.2 are on real disks with that name, and
# "Use existing..." adopts a folder whatever wrote it. Being stricter than the
# tool we drive would buy nothing and would cost exactly what it cost then.
COMPOSE_FILENAMES: tuple[str, ...] = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
)


def compose_file(server_dir: Path) -> Path | None:
    """The folder's compose file, or None if it holds no install.

    Answers what Compose itself would answer, in the same order, so a folder
    holding two spellings resolves to the one the daemon will actually load.
    The order is measured, not guessed: with all four present, Compose v5.3.1
    reports "Found multiple config files with supported names: compose.yaml,
    compose.yml, docker-compose.yml, docker-compose.yaml" and uses the first.
    Note the last two - `.yml` before `.yaml`, the opposite way round from the
    first pair, which is why an earlier version of this tuple had them swapped.
    `is_file()` rather than `exists()`: a directory named `compose.yml` is not
    an install.
    """
    for name in COMPOSE_FILENAMES:
        candidate = server_dir / name
        if candidate.is_file():
            return candidate
    return None


# How the app recognised sudo's own password prompt: the bash engine set
# `SUDO_PROMPT` to this prefix plus a random per-install token, so an exact
# match proved the text came from sudo rather than from build output, in any
# locale. 7.2 deleted that engine and the native path asks through
# `platform.SUDO_PASSWORD_QUESTION` instead, so nothing writes this spelling
# any more — but `install_wiring._terminal_prompter()` and `ui.widgets.prompt`
# still recognise it, and a prompt that is not recognised as sudo's is one
# echoed to the screen as it is typed (review, 2026-08-28).
SUDO_PROMPT_PREFIX = "[sudo via Yu'lon "


class InstallerError(RuntimeError):
    """The install could not start or did not finish (message is user-readable)."""


class DockerUnavailableError(InstallerError):
    """No Docker daemon is reachable and automatic provisioning is not available yet."""


class DockerNeedsReLoginError(DockerUnavailableError):
    """Docker WAS set up; this login session cannot see the group it was given.

    A subclass rather than a sibling, because to everything that only needs to
    stop the install these two are one event — but they are not one event to
    the user, and they were being told the wrong one. `usermod -aG docker`
    cannot change the supplementary groups of a process that is already
    running, so the very run that provisions Docker correctly is the run that
    cannot use it: the remedy is a new login, not a second attempt at
    installing anything (live Ubuntu gate, press 1, 2026-08-30).
    """


class UnsupportedPlatformError(InstallerError):
    """This entry's installer does not run on this platform (roadmap 6.1)."""


def docker_unavailable(report: platform.ProvisionReport) -> DockerUnavailableError:
    """The refusal for a provision that ran and left no daemon THIS process can reach.

    THREE outcomes wore one sentence until 7.1, and it was the wrong one for
    the two a first-time Linux user actually meets. `docker_group == "granted"`
    means the group join RAN and succeeded — the engine is installed and the
    account is a member — and the only thing between the user and a working
    install is that a process cannot acquire a supplementary group it was
    granted after it started. Telling them Docker "could not be set up
    automatically" is the opposite of what happened, and it is the first thing
    this engine ever says to them (live Ubuntu gate, press 1, 2026-08-30:
    `docker.io` 29.1.3 active, `pk` added to group 124, and the install
    reported as failed).

    `already-member` is the same event one press later and is the state a real
    user is in most often. `_docker_group_member()` asks `id -nG <user>`, which
    reads the group database rather than the running process, so the press
    AFTER the join reports `already-member` with the daemon still unreachable -
    and that branch produced "could not be set up automatically. Install
    Docker, start it, and try again." with no manual steps at all.
    `platform.DockerGroupOutcome`'s docstring had already put the two on the
    same side: "only `granted` and `already-member` ... may print the
    log-out-and-back-in line". They get different sentences, because they need
    different next actions: `granted` knows why this session cannot see the
    group, and `already-member` cannot tell "you have not logged out yet" from
    "the service is down", so it names both.

    Built here rather than at either call site so the script path and the
    native spine cannot drift: both had the same sentence, so both had the
    same defect.
    """
    details = " ".join(report.manual_steps) or "; ".join(report.skipped)
    if report.docker_group == "granted":
        # The remedy this branch reads out is `platform.DOCKER_GROUP_RELOGIN_STEP`,
        # arriving through `details`. The fallback under it never rendered for a
        # user: `_ensure_docker_linux()` is the only producer of `granted`, and
        # it appends that step to `manual_steps` for the outcome unconditionally,
        # so `details` was non-empty on every report that ever reached here
        # (checked against `platform.py`, 2026-09-02).
        #
        # Kept anyway, and deliberately NOT folded into the static sentence
        # above. This function words a `ProvisionReport` and cannot see who
        # built one — `manual_steps` defaults to `()` — so without the fallback
        # a hand-built or future report would tell the user their session cannot
        # see the group and then name nothing to do about it. Writing the remedy
        # inline instead, the way the `already-member` branch below does, would
        # print it TWICE for the report production actually sends: that is the
        # duplication the 2026-08-31 review found on that other branch, and the
        # reason `platform.py` keeps the step out of `already-member`'s steps.
        #
        # So the sentence under test is the one production produces:
        # `test_regroup.py::test_the_restart_is_offered_before_the_logout`
        # asserts the ordering against a report CARRYING that step, and keeps a
        # second case for the empty-steps fallback rather than only that one.
        return DockerNeedsReLoginError(
            "Docker is installed and set up. It cannot be used from this session yet: your "
            "account was added to the docker group, and a session that was already open does "
            "not pick up a new group. "
            + (
                details
                or "Restart Yu'lon and start the install again. Log out and back in if a "
                "restart is not enough."
            )
        )
    if report.docker_group == "already-member":
        # Two causes, and the report cannot tell them apart, so both are named,
        # commoner first. `ensure_docker()` returns early when a daemon answers,
        # so reaching here at all means it did not - which for a member of the
        # group is either a session opened before the join, or a service that is
        # down.
        #
        # The log-out instruction is INSIDE this sentence and `platform.py`
        # deliberately keeps it out of `manual_steps` for this outcome, or
        # `details` appends a second copy of it after the "if you have already
        # done that" clause - which sends the one user that clause exists for
        # straight back to the advice it just ruled out (review, 2026-08-31).
        return DockerNeedsReLoginError(
            "Docker is installed and your account is already in the docker group, but this "
            "session still cannot reach the daemon. A session that was open before the group "
            "was granted does not pick it up: restart Yu'lon and start the install again, or "
            "log out and back in if a restart is not enough. If you have already done that, "
            "the Docker service is not running - start it and try again."
            + (f" {details}" if details else "")
        )
    return DockerUnavailableError(
        "Docker isn't available and could not be set up automatically. "
        + (details or "Install Docker, start it, and try again.")
    )


def provision_lines(report: platform.ProvisionReport) -> Iterator[str]:
    """What provisioning DID, said on every path and not only the failing one.

    The report was read exclusively inside the refusal, so a provision that
    worked threw `done`, `skipped` and `manual_steps` away — and the one manual
    step that matters most is produced by the SUCCESS case: a user who has just
    consented to the docker-group join needs to be told to log out and back in.
    They saw that sentence only if something else went wrong (review finding,
    confirmed against the 2026-08-30 gate).

    `skipped` is deliberately not echoed: on Linux `ensure_docker()` already
    folds it into a `manual_steps` line that says WHY each one was skipped, and
    printing both says everything twice.
    """
    if report.done:
        yield "Docker setup did: " + "; ".join(report.done)
    for step in report.manual_steps:
        yield f"Still to do: {step}"


@dataclass(frozen=True)
class InstallOptions:
    """What the user decided before clicking install.

    `reinstall` left with the rule table in 7.2: the only thing that ever read
    it was the bash "Remove it and start fresh?" rule, and the native engine
    never removes a folder — it resumes into it or refuses it
    (`StagedInstaller._guard`).
    """

    server_dir: Path | None = None
    client_dir: Path | None = None


_PLATFORM_NAMES: dict[str, str] = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}


def platform_names(platforms: Iterable[str]) -> str:
    """Platform ids as user-facing copy: `("linux", "macos")` → `"Linux or macOS"`."""
    names = [_PLATFORM_NAMES.get(p, p) for p in platforms]
    if len(names) < 2:
        return names[0] if names else "another platform"
    return f"{', '.join(names[:-1])} or {names[-1]}"


def unsupported_platform_message(entry: CatalogEntry, platform_id: str) -> str:
    """Why this server cannot be installed here, in the user's words (roadmap 6.1)."""
    supported = platform_names(entry.install.platforms)
    where = platform_names([platform_id])
    return (
        f"{entry.name} cannot be installed on {where} yet: its installer needs "
        f"{supported}. Nothing was started. Install it on {supported} for now — "
        "a native path for this platform is planned."
    )


def generated_compose_files(server_dir: Path) -> tuple[str, ...]:
    """The compose files in `server_dir` that THIS app wrote, by name, in `-f` order.

    Not `compose_file()`, and the difference is the whole point. That one asks
    "could Compose bring something up here?", which is the right question for
    "Use existing…" adopting a folder of unknown provenance, and it answers on
    any of the four filenames Compose itself accepts. This asks "did the
    engine's own compose stage run?", and on a cancelled install the two
    disagree: the AzerothCore repository ships a `docker-compose.yml` at its
    root, so a WotLK `clone-core` lays one down before `composegen` writes a
    byte (`composegen.write_plan()`'s `replaceable` argument exists for exactly
    that file).

    Measured on yulon-ubuntu 2026-09-05: a WotLK install stopped 20 s into
    `clone-core` left a folder whose only compose file was upstream's own,
    git-tracked and unmodified
    (`pyplan/gates/7.2-ubuntu-2026-09-05/widget-cancel-folder-after.txt`).
    That is one family on one box. Whether the CMaNGOS repositories ship one
    too has not been measured, and nothing here needs it to: the question this
    function answers is about the marker, not about upstream's habits, so a
    family whose source ships no compose file simply never exercises the
    disagreement.

    `composegen.is_ours()` is the marker rule and is reused rather than
    respelled, but it cannot be called alone here: it answers True for a file
    that is not there, which is right for "may I write this?" and exactly
    backwards for "is this here and mine?". Hence the `is_file()` first.
    """
    return tuple(
        name
        for name in composegen.COMPOSE_FILES
        if (server_dir / name).is_file() and composegen.is_ours(server_dir / name)
    )


def cancelled_install_message(entry: CatalogEntry, server_dir: Path) -> str:
    """What Stop actually did, what it did not, and which button to press next.

    Three things are easy to imply and all three are false. The app has not
    remembered the folder — which it did until this existed. Stopping undoes
    nothing and tidies nothing away. And terminating the compose client does not
    stop a build that had started: BuildKit finishes the step it is on inside
    the daemon. That last one is deliberate rather than a wart — those layers
    are cached and are what makes a second attempt cheap — so the copy says so,
    because a message implying an instant halt is what sends someone to `docker
    builder prune` to tidy up, throwing away the hours it would have saved
    (`phase6-decisions.md`).

    What it deliberately does NOT promise is that files are there. Both
    outcomes were measured on the same machine on the same day: cancelled after
    the source clone finished, 2.3 GB stayed; cancelled 1.3 s in, `git` removed
    its own half-written target and the folder was gone. So the copy points at
    the folder and lets the user look, rather than asserting a state it cannot
    know (install gate, 2026-08-23).

    **The advice below is two independent halves, and each is decided by the
    thing that actually gates it.** They were one split on `compose_file()`
    until the 7.10 widget-cancel run drove the whole path with real clicks
    (yulon-ubuntu 2026-09-05, `pyplan/gates/7.2-ubuntu-2026-09-05/`): 15 checks
    green, and a modal that got both halves wrong on the folder in front of it.

    *"Use existing…"* is offered when, and only when, `compose_file()` answers.
    That is not a preference: `attach_existing()` gates on exactly that reading,
    so an offer made on anything else is an offer the app then refuses.
    `generated_compose_files()` chooses the WORDING, and it took two goes to get
    that division right. The 7.10 split asked `compose_file()` alone and so
    fired on the `docker-compose.yml` the clone stage brings down with the
    source (`widget-cancel-folder-after.txt`: that file, nothing built, and a
    modal telling the user to adopt it — which `attach_existing()` would have
    done, growing a tab for a server that did not exist). `2a4f0cab` moved the
    offer onto `generated_compose_files()` and was worse in the other
    direction: a folder with `.git`, `src/` and an UNMARKED
    `docker-compose.yml` — a bash-era install, a hand-written compose file,
    anything from before 7.2, since nothing before 7.2 marked what it wrote —
    got no offer at all and was told to delete itself, while `attach_existing()`
    would have adopted it. It also left `ours` deciding an offer on its own, and
    `ours` does not imply `compose_file()`: `docker-compose.override.yml` is in
    `composegen.COMPOSE_FILES` and not in `COMPOSE_FILENAMES`, so a folder
    holding a marked override and no base rendered "nothing is lost" and
    "Delete <dir>" in consecutive sentences
    (`test_the_app_never_says_nothing_is_lost_about_a_folder_it_names_for_deletion`).
    That folder shape has NO install that produces it, and the branch for it
    says so: `composegen.write_plan()` walks `(BASE_FILE, OVERRIDE_FILE,
    BUILD_FILE)` in that order and always writes or keeps the base first, so
    the only route is a base file deleted by hand. Measured, not assumed — with
    `raise AssertionError` as the branch body the whole suite on m910q
    2026-09-05 was `1 failed, 2562 passed, 9 skipped` and the single entrant
    was the test written for it. It stays because a hand-deleted base is a
    folder a person can be standing in front of, and the alternative render for
    it is a flat "Delete <dir>" over this app's own files.

    **A record does NOT prove the folder began empty, and the copy said it
    did.** The unmarked-compose wording puts two readings to the user —
    installed before this attempt, or brought down by this attempt's clone. For
    a few hours on 2026-09-05 a branch here claimed `native.STATE_FILE` settled
    that, on the ground that `_claim_before_writing()` writes one only when
    `started_empty` is true and that the one non-empty folder `_claim_folder()`
    lets past — a `.git` — is stopped at the clone by
    `refuse_unowned_checkout()`. Measured on m910q 2026-09-05, that is false
    for three of the four shipped games. `wow-tbc` driven through
    `CmangosInstaller.run()` into a folder holding a user's own `.git`,
    `my-notes.txt` and `docker-compose.yml`, Stop pressed after
    `clone-sources`: the record was there and the modal told the user their own
    file "came down with the server's source". `_run_one()` writes the record
    after EVERY recorded stage, `started_empty` or not, and
    `refuse_unowned_checkout()` only ever sees the server dir when a source's
    `dest` IS the server dir — `"."`, which of the shipped entries only
    `wow-wotlk` spells. TBC, Vanilla and Tortoise clone into `src/`, so nothing
    asks about the folder itself at all.

    **The discriminator is the family's clone layout, so the family is an
    input.** Hence `entry` where this took a display name until 2026-09-05.
    Where a source lands in the server dir itself, an unmarked compose file
    there with a record beside it did come down with the clone: the only other
    way into that folder is a checkout, and `refuse_unowned_checkout()` refuses
    one before any stage records anything
    (`test_the_clone_artefact_reading_is_given_only_where_the_clone_lands_there`).
    Where no source lands there, nothing this attempt downloads can put a
    compose file at the root, so it was already there and the adoption is the
    right offer, record or no record
    (`test_no_source_of_a_shipped_game_is_cloned_into_the_server_dir_except_wotlks`).
    Only the first case with no record leaves two readings nothing on disk
    separates, and there both are said and the user looks.

    The engine defect underneath this is not the copy's to fix and is not fixed
    here: `_claim_folder()` exempts any folder holding a `.git`, and only a
    `dest: "."` family redeems that exemption at the clone, so a CMaNGOS
    install runs to completion inside a user's own checkout (measured the same
    day; `pyplan/checklist.md`). Both branches above stay true whichever way
    that is closed, because neither reads the record for emptiness.

    *"Press Install again"* is offered on the same record, which is not implied
    by source on disk: a folder can hold a whole checkout and no record. What
    the copy must NOT do is explain that with a story about `clone-core`. It
    used to say a clone still running when Stop was pressed "takes that record
    with it", which is a defect being described as a design — the record is
    removed at the START of `clone-core` on every fresh install, whether or not
    anyone presses Stop, because `_clone_core()` clones into the server dir and
    the seam empties a destination it is about to clone into
    (`test_the_clone_that_fills_the_server_dir_takes_the_ownership_record_with_it`).
    A message is not the place to file that.

    **The refusal names the check that actually stops the folder in front of
    it.** Two different ones, and the copy gave both of them the git reason
    until 2026-09-05. With a `.git`: `refuse_unowned_checkout()`, whose reason
    IS fetch-and-reset over a checkout that may be yours — measured, `python -m
    yulon.install_wiring wow-wotlk --server-dir …` exited 1 with "there is
    already a git checkout of … and there is no record here of an install this
    app made" (`cycle2-pressA2-refused-existing-checkout.log`). Without one:
    `_claim_folder()`'s "is not empty and was not created by this app", raised
    in preflight, where there is no checkout to fetch and nothing to reset. Both
    are driven at a call site in
    `test_the_engine_refuses_the_folder_a_cancelled_clone_leaves`.

    **"nothing is lost" and a deletion never appear in one message, and one
    shared reading is what used to put them there.** `2a4f0cab` argued the pair
    was impossible because `adoptable` decided both the offer and the delete;
    deciding both on one reading is exactly what makes them SIMULTANEOUS.
    Measured on m910q 2026-09-05 on a folder holding a marked
    `docker-compose.yml` and no record — the shape a user reaches by deleting
    `native.STATE_FILE`, which is what the UNKNOWN refusal tells them to do —
    the render carried "nothing is lost" and "delete <dir>" two sentences
    apart. The rule is now on the SENTENCE, not on a reading:
    `promised_nothing_lost` is the first branch's own condition, named once,
    and the remedy names a deletion only where it is False. Asserted over every
    reachable folder shape rather than on the one that was reported, in
    `test_no_folder_shape_is_offered_adoption_and_deletion_at_once`.

    With the record there the resume is real and was measured the same night:
    "Using /home/pk/gate72-cycle2 (resuming)", "Already finished: clone-core,
    clone-modules, generate-compose" (`cycle2-pressB.log:26`).

    The pre-7.2 wording is gone for good and must not come back. The bash
    installer's line 961 found no built worldserver image, took the
    existing-folder branch, asked "Remove it and start fresh? (y/n):" — and
    `PROMPT_RULES` answered "n", because `InstallOptions.reinstall` was False and
    nothing in the GUI ever set it. The script printed "Keeping existing install
    — exiting." and exited 0, which the view read as a SUCCESS: it pinned a
    compose project name into a half-cloned folder and remembered a server that
    did not exist. 7.2 deleted that engine.
    """
    # Local import: `native.py` imports this module for the options and the
    # errors, so naming it at module scope would be a cycle.
    from yulon.catalog import native

    parts = [
        f"Stop was pressed, so {entry.name} has NOT been remembered as an install and the app "
        f"will not show a tab for it. Stopping undoes nothing and tidies nothing away — look "
        f"in {server_dir} to see what the installer had got to (a download it was in the "
        "middle of may have removed its own leftovers; anything already finished stays). If "
        "the build had started, Docker keeps finishing the step it was on in the background — "
        "that is deliberate, and the finished pieces are what make a second attempt much "
        "faster, so do not clear Docker's build cache to tidy up."
    ]
    record = server_dir / native.STATE_FILE
    # This attempt got at least one recorded stage in, or started in a folder
    # that was its to fill. NOT evidence that the folder began empty:
    # `_run_one()` writes this after every recorded stage regardless
    # (measured on `wow-tbc`, m910q 2026-09-05 — see the docstring).
    claimed = record.is_file()
    # Whether a clone of THIS game's sources lands in the server dir itself,
    # which is the only way a compose file this app did not write can be there
    # because this attempt put it there. `EmulatorSource.dest` documents `"."`
    # as exactly that; normalised rather than compared to the literal, because
    # `"./"` passes that field's validator and means the same folder.
    clones_into_server_dir = any(
        PurePosixPath(source.dest) == PurePosixPath(".") for source in entry.emulator.sources
    )
    # Where the sources DO go when they do not go here — named in the copy so
    # "nothing this install downloads goes there" is checkable by the user.
    sources_land_in = next(
        (
            source.dest
            for source in entry.emulator.sources
            if PurePosixPath(source.dest) != PurePosixPath(".")
        ),
        "",
    )
    ours = generated_compose_files(server_dir)
    # What `attach_existing()` gates on, asked here for the same reason it is
    # asked there — it is the whole of whether "Use existing…" can take this
    # folder — and NOT conflated with `ours`, which is whether this app wrote
    # what is in it.
    adoptable = compose_file(server_dir)
    # `refuse_unowned_checkout()`'s input, which is what decides WHICH refusal
    # the next press meets. A pure `is_dir()`, exactly as the engine asks it.
    has_checkout = (server_dir / ".git").is_dir()
    try:
        leftovers = bool(
            server_dir.is_dir() and native._listing(server_dir, ignoring=native.STATE_FILE)
        )
    except InstallerError:
        # A folder this app cannot list is a folder `_claim_folder()` cannot
        # list either, and it refuses on the same `OSError` — so the refusal
        # branch is the true answer here, not the fallback it looks like.
        # `native._listing()` is called rather than a bare `iterdir()` for that
        # reason: the engine's answer to "is this folder empty" and the copy's
        # must not be able to differ, including on the folder neither of them
        # can read. Both answers are driven, on one unreadable folder, in
        # `test_a_folder_the_copy_cannot_list_is_refused_rather_than_called_empty`.
        leftovers = True

    # The one branch that says "nothing is lost", named once so the two places
    # that have to agree with it — the "If it had not" opener and the remedy —
    # cannot drift from it. They did: `adoptable` alone gated the delete, and
    # `adoptable` alone is true in this branch too.
    promised_nothing_lost = adoptable is not None and bool(ours)

    if promised_nothing_lost:
        parts.append(
            f"The compose files this app writes are there ({', '.join(ours)}), so if the build "
            f'had already finished the server may be built and even running: press "Use '
            f'existing…", choose {server_dir}, and the app will manage it from a tab — nothing '
            "is lost."
        )
    elif ours:
        parts.append(
            f"Compose files this app wrote are there ({', '.join(ours)}), but "
            f"{composegen.BASE_FILE} is not, and that is the one Compose loads — so "
            f'"Use existing…" cannot take {server_dir} as it stands.'
        )
    elif adoptable is not None and not clones_into_server_dir:
        # Nothing this attempt downloads can have put that file at the root, so
        # there is no second reading to put to the user and no reason to
        # withhold an adoption `attach_existing()` would make.
        parts.append(
            f"There is a {adoptable.name} in {server_dir} that this app did not write, and "
            f"nothing this install downloads goes there — {entry.name}'s source is cloned into "
            f"{sources_land_in}/ under it. So that file was already there before this attempt: "
            f'press "Use existing…", choose {server_dir}, and the app will manage it from a '
            "tab."
        )
    elif adoptable is not None and claimed:
        parts.append(
            f"There is a {adoptable.name} in {server_dir} that this app did not write, and it "
            f"came down with the server's source: {entry.name}'s source is cloned into that "
            f"folder itself, and {record} is this attempt's own record of having got that far. "
            "There is no server behind that file."
        )
    elif adoptable is not None:
        parts.append(
            f"There is a compose file in {server_dir} that this app did not write "
            f"({adoptable.name}). If it was already there before this attempt — a server "
            f"installed by an older version of this app, or one you set up by hand — press "
            f'"Use existing…", choose {server_dir}, and the app will manage it from a tab. If '
            f"this attempt was downloading into an empty folder, that file came down with the "
            "server's source and there is no server behind it."
        )
    if claimed:
        # "If it had not" only when the sentence before it is the one that said
        # "if the build had already finished" — which is the first branch, not
        # merely `ours`: on a folder with an override of ours and no base the
        # sentence above says the opposite, and the conditional would refer to
        # a promise nobody made.
        opener = (
            "If it had not, press Install again" if promised_nothing_lost else "Press Install again"
        )
        parts.append(
            f"{opener} and choose {server_dir}: the installer carries on from the "
            f"last stage recorded in {record}, and a stage is only skipped after what it left "
            "on disk has been checked."
        )
    elif leftovers:
        why = (
            "it holds a git checkout: with no record here of an install this app made, the app "
            "cannot tell its own half-finished download from a checkout you made yourself, so "
            "it stops rather than run `git fetch` and `git reset --hard` over your work"
            if has_checkout
            else "it has files in it: the app will not write into a folder that is not empty "
            "and was not created by this app"
        )
        # A deletion is named only where the message has NOT already said
        # "nothing is lost" about this folder. Chosen on that sentence rather
        # than on `adoptable`, which is true in the branch that says it.
        if promised_nothing_lost:
            remedy = f'Use "Use existing…" on {server_dir} instead; nothing here needs deleting.'
        elif adoptable is not None:
            remedy = (
                f"If there is no server in it after all, delete {server_dir} and press Install "
                "again to start over."
            )
        else:
            remedy = f"Delete {server_dir}, then press Install again to start over."
        parts.append(
            f"Do not press Install again on this folder — the app will refuse it. There is no "
            f"{native.STATE_FILE} in {server_dir}, {why}. {remedy}"
        )
    else:
        parts.append(
            f"There is nothing in {server_dir} to carry on from. Press Install again and "
            "choose it to start over."
        )
    return " ".join(parts)


# `docker_available()` used to live here as `runner.run(["docker", "info"])`,
# which is `platform.docker_ready()` written a second time (style-guide §4) —
# and the copy that never learned about `docker_programs()`. Deleting it rather
# than fixing it is what stops the pair drifting again: the preflight gate and
# the provisioning probe now agree by construction, so an install can no longer
# be refused with "Docker is not running" on a Windows box where
# `ensure_docker()` had just proved that it is.


class InstallEngine(Protocol):
    """What a catalog view can drive, whichever family engine it got.

    Every `native.StagedInstaller` family satisfies it, which is the whole
    reason `catalog_view.py`, `log_panel.py` and the job runner needed no
    changes for roadmap 6.2 or Phase 7.
    """

    def preflight(
        self,
        options: InstallOptions,
        cancel: threading.Event | None = None,
        *,
        ask: runner.Prompter | None = None,
    ) -> None: ...

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: runner.Prompter | None = None,
    ) -> Iterator[str]: ...


def installer_for(
    entry: CatalogEntry,
    *,
    platform_id: Callable[[], str] = platform.detect,
    installers_root: Path = DEFAULT_INSTALLERS_ROOT,
    import_probe: docker.ImportProbe | None = None,
    reset_unfinished: docker.ResetUnfinished | None = None,
) -> InstallEngine:
    """The engine that installs `entry`. The only place that decides.

    One rule since 7.2, read from `catalog.json` rather than from what OS this
    is (style-guide §3): `install.native.family` names the engine, through
    `families.family_for()`. The platform is NOT consulted here — every family
    engine runs on every platform, and an entry that does not support this one
    (`install.platforms`) is refused by the engine's own preflight, from the
    one place that words it (`unsupported_platform_message()`). That keeps the
    refusal on the worker thread with the rest of preflight, where the view
    already expects it (`catalog_view.start_install()` calls this factory
    synchronously and never expects it to raise for a shipped entry).

    Until 7.2 there was a second rule and a third state. An entry with no
    `native` block ran its bash script, and an entry whose family THIS BUILD
    had no engine for fell back to that script rather than being refused —
    a bridge that existed because catalog data arrives before the engine that
    reads it, and it did between G.4 and K.8. K.8 registered `cmangos`, which
    left every shipped entry dispatching to a family engine and the fallback
    reachable by nothing; F.3 deleted the script it fell back to. An entry
    whose family is unregistered now gets `family_for()`'s "install family this
    app does not have" sentence, which is what that state has always been.

    An entry with no `native` block cannot be given an engine at all: there is
    no family to name. That is a catalog-authoring error, not the user's, so it
    is raised here rather than deferred; `test_catalog.py` pins that every
    shipped entry with a non-empty `platforms` carries a `native` block, so the
    app's Install button — disabled off `platforms` — cannot reach this branch.
    The CLI harness (`install_wiring.main()`) can, and catches it (exit 1).

    `import_probe`/`reset_unfinished` are per-game seams the CALLER supplies
    (`install_wiring.py` wires `controller_wow_wotlk.repair`), because
    `catalog/` must not import a controller package.

    Imported inside the function on purpose: `native.py` imports this module
    for `InstallOptions` and the error types, so naming it at module scope
    would be a cycle. The alternative — a fourth module holding three
    exceptions and a dataclass — buys nothing but an import.
    """
    from yulon.catalog import native
    from yulon.catalog.families import family_for

    if entry.install.native is None:
        raise InstallerError(
            f"{entry.name} cannot be installed yet: its catalog entry has no `install.native` "
            "section. Nothing was started."
        )
    engine = family_for(entry)
    logger.debug(f"{entry.id} installs through {engine.__name__}")
    return engine(
        entry,
        installers_root=installers_root,
        import_probe=import_probe,
        reset_unfinished=reset_unfinished,
        seams=native.Seams(platform_id=platform_id),
    )
