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
`catalog_view.py` shows it after Stop.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon import docker, platform, resources, runner
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
        return DockerNeedsReLoginError(
            "Docker is installed and set up. It cannot be used from this session yet: your "
            "account was added to the docker group, and a session that was already open does "
            "not pick up a new group. "
            + (details or "Log out and back in, then start the install again.")
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
            "was granted does not pick it up: log out and back in, then start the install "
            "again. If you have already done that, the Docker service is not running - start "
            "it and try again." + (f" {details}" if details else "")
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


def cancelled_install_message(entry_name: str, server_dir: Path) -> str:
    """What Stop actually did, and what it did not (roadmap 6.5 "honest cancel copy").

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

    The recovery advice is split on the compose file, and until 7.2 both halves
    had to refuse to promise resumption. The bash installer's line 961 found no
    built worldserver image, took the existing-folder branch, asked "Remove it
    and start fresh? (y/n):" — and `PROMPT_RULES` answered "n", because
    `InstallOptions.reinstall` was False and nothing in the GUI ever set it. The
    script printed "Keeping existing install — exiting." and exited 0, which the
    view read as a SUCCESS: it pinned a compose project name into a half-cloned
    folder and remembered a server that did not exist.

    7.2 deleted that engine. `native.StagedInstaller` records every finished
    stage by name in `native.STATE_FILE` and re-checks what is on disk before it
    skips one, so pressing Install again on the same folder carries on rather
    than doing nothing — and the copy now says that, in both halves. "Use
    existing…" keeps its half because it answers a different question: it adopts
    a build that had in fact FINISHED, without resuming anything, which is the
    case where Stop threw away hours (review, 2026-08-23; rewritten 7.2).

    The compose file is looked up through `compose_file()` rather than by the
    one name: TBC and Vanilla installs are called `compose.yml`, and a check
    stricter than Compose's own reported "nothing there" for a finished install
    of two of the four games.
    """
    # Local import: `native.py` imports this module for the options and the
    # errors, so naming it at module scope would be a cycle.
    from yulon.catalog import native

    lead = (
        f"Stop was pressed, so {entry_name} has NOT been remembered as an install and the app "
        f"will not show a tab for it. Stopping undoes nothing and tidies nothing away — look "
        f"in {server_dir} to see what the installer had got to (a download it was in the "
        "middle of may have removed its own leftovers; anything already finished stays). If "
        "the build had started, Docker keeps finishing the step it was on in the background — "
        "that is deliberate, and the finished pieces are what make a second attempt much "
        "faster, so do not clear Docker's build cache to tidy up."
    )
    if compose_file(server_dir) is not None:
        return (
            f"{lead} The source is there. If the build had already finished, the server may "
            f'be built and even running: press "Use existing…", choose '
            f"{server_dir}, and the app will manage it from a tab — nothing is lost. If it "
            f"had not, press Install again and choose {server_dir}: the installer carries on "
            f"from the last stage recorded in {server_dir / native.STATE_FILE}, and a stage is "
            "only skipped after what it left on disk has been checked."
        )
    return (
        f"{lead} The installer had not got as far as writing a compose file "
        f"(compose.yml or docker-compose.yml), so there is nothing there for the app to "
        f"manage yet. Press Install again and choose {server_dir} to carry on — a source "
        f"clone that finished is updated, not fetched again. Delete {server_dir} only if you "
        "want a clean start."
    )


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
