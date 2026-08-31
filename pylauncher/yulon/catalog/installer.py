"""Install orchestrator (Phase 3a): drive one catalog entry's existing `install-*.sh`.

Phase 3a wraps the scripts we already ship instead of reimplementing them
(README §7/§9): the orchestrator resolves the entry's script, answers the
script's prompts from a typed rule table so no shell interaction is needed,
and streams the output up to whoever is listening (a CLI today, the
`log_panel` in Phase 4). This module never downloads anything itself — the
user's own client directory is *passed in* (README §3a) — and it never
contains per-game logic: what differs per game is `catalog.json` data.

That is a claim about this module, not about the whole install. An earlier
wording said "never downloads client assets" full stop, which is not true of
what it drives: the WotLK script fetches AzerothCore's own client-data archive
(maps, vmaps, mmaps, DBC) into a Docker volume, which is why `wow-wotlk` is the
one entry with `requires_client_dir` false and is never asked for a folder.
That archive is server-side data, not the client a player logs in with, and
nothing here ships or fetches the latter (review, 2026-08-23).

Docker provisioning is wired in (roadmap 3.3 → 5.1): if no daemon answers,
`platform.ensure_docker()` is asked to provide one; when it cannot (needs a
reboot, a password, a manual install) that is a clean, logged, catchable
`DockerUnavailableError` carrying the report's manual steps — never a crash
or a silent hang.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon import docker, platform, resources, runner
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

# Where `catalog.json`'s install scripts resolve from (roadmap 6.0).
DEFAULT_INSTALLERS_ROOT = resources.installers_dir()

# Every filename Docker Compose accepts for a project's compose file, in its own
# precedence order.
#
# Not ours to shorten: the app only ever looked for `docker-compose.yml`, which
# is what the WotLK and Tortoise scripts write - while the TBC and Vanilla ones
# write `compose.yml`. The result was that a finished install of those two was
# invisible to "Use existing..." AND was thrown away by the remember check at
# the end of a multi-hour install, both reporting that nothing was installed.
# Being stricter than the tool we drive buys nothing and costs exactly that.
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


# How many of the script's last output lines a failure message carries (roadmap 6.1).
_ERROR_TAIL_LINES = 12
# What the scripts see as their terminal when the app was not started from one.
DEFAULT_TERM = "xterm-256color"


# The stable half of `Installer.sudo_marker`. The token after it is random and
# per-install, so this prefix is the only part a module-level prompter can match
# on - and matching is what keeps sudo's prompt hidden while the two y/n consent
# rules stay visible (review, 2026-08-28).
SUDO_PROMPT_PREFIX = "[sudo via Yu'lon "


class InstallerError(RuntimeError):
    """The install could not start or did not finish (message is user-readable)."""


class DockerUnavailableError(InstallerError):
    """No Docker daemon is reachable and automatic provisioning is not available yet."""


class UnsupportedPlatformError(InstallerError):
    """This entry's installer does not run on this platform (roadmap 6.1)."""


@dataclass(frozen=True)
class InstallOptions:
    """What the user decided before clicking install."""

    server_dir: Path | None = None
    client_dir: Path | None = None
    reinstall: bool = False


class AskTheUser:
    """A rule's answer when the app has no business choosing. See `PROMPT_RULES`."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "ASK_THE_USER"


ASK_THE_USER = AskTheUser()
"""Route this prompt to the person, because neither answer is the app's to give.

Not a general escape hatch: a rule that opens a dialog is the shape that made
the old prompt heuristic dangerous, so the bar is narrow and stated. A question
qualifies only if its two answers cost the user different things that the app
cannot weigh for them, and only if its pattern pins the QUESTION rather than
the subject — every rule here is an unanchored `re.search` over a line, first
match wins, so "matches the words" and "matches the question" are not the same
bar. In practice that means ending the pattern at the `(y/n)` suffix
`ask_yes_no` prints, which is the one thing a warning paragraph about the same
subject never carries (this used to claim an EXACT whole-line match, which
nothing here enforces — review, 2026-08-23).

Two clear it today. The installers' docker-group question: yes grants
root-equivalent access silently — the precise thing upstream's 1.4.4 security
change added consent for — while no leaves the user outside the docker group,
so the launcher's own `docker` calls fail with permission denied and the app
has quietly broken itself instead. And immutable Fedora's rpm-ostree question,
which reboots the machine ten seconds after a yes.
"""


@dataclass(frozen=True)
class PromptRule:
    """`pattern` (regex, searched in the ANSI-stripped prompt) → the stdin answer."""

    pattern: str
    answer: str | Callable[[InstallOptions], str] | AskTheUser
    note: str = ""


# How the app answers the scripts' questions. First match wins. Optional and
# destructive offers are declined; everything that merely gates progress is
# accepted. The shared prompt helpers (`ask_yes_no`, `press_enter`,
# `choose_install_dir`) are identical across the four installers, so one
# table serves all of them.
#
# Read the last rule as the real policy: anything ending in `(y/n)` that no
# earlier rule claims is answered YES, unseen. That is not a safe default, it
# is a workable one — a question nobody answers parks the install forever,
# because nothing here has a timeout — so every destructive question a script
# can ask has to be named ABOVE it, and a new one that is not named is
# consented to on the user's behalf. Two were found that way while driving a
# real install through the Catalog's own button (2026-08-23); see their rules.
PROMPT_RULES: tuple[PromptRule, ...] = (
    PromptRule(
        r"Install path:",
        lambda o: o.server_dir.as_posix() if o.server_dir else "",
        "blank = the script's default dir; POSIX form — the scripts run under bash",
    ),
    PromptRule(
        r"Enter path to your .*client folder",
        lambda o: o.client_dir.as_posix() if o.client_dir else "",
        "the user's own client (README §3a)",
    ),
    PromptRule(
        r"Remove it and start fresh\?", lambda o: "y" if o.reinstall else "n", "existing server dir"
    ),
    PromptRule(r"Type yes to reset the keyring", "yes", "Steam Deck pacman keyring repair"),
    # Anchored: `respond()` sees every line, and the scripts also print
    # "Leave blank and press ENTER to use the default location." — an
    # unanchored match answered that *hint* and the blank line was consumed
    # by the `Install path:` prompt (Phase 3 live-gate finding, 2026-08-20).
    PromptRule(r"^\s*Press ENTER", "", "'to continue' / 'when done creating accounts'"),
    PromptRule(r"press ENTER to shut down", "", "end of install: let the script finish"),
    PromptRule(r"Continue anyway\?", "n", "the script found the wrong client"),
    PromptRule(r"Open the GitHub README", "n"),
    PromptRule(r"Download wow-manage\.sh", "n"),
    PromptRule(r"stop the server now\?", "n"),
    # Declined on purpose, and it must sit above the `(y/n)` catch-all that
    # would otherwise answer "y". The installer scripts now OFFER to stop
    # whatever is holding the server ports, which is the right question to put
    # to a person at a terminal - but not one the app may answer on their
    # behalf: the thing it would stop is a server someone may be playing on
    # this second, and no install is worth that. The GUI makes the same offer
    # itself, on the tab, where the user can see what they are agreeing to
    # (`controller_view._offer_to_stop_the_other_server`).
    PromptRule(
        r"Stop the other server and continue\?",
        "n",
        "the app never stops a running server to get an install through",
    ),
    # Must sit ABOVE the `(y/n)` catch-all, which would otherwise answer "y" and
    # grant root-equivalent access without anyone being asked — exactly what
    # upstream's 1.4.4 security change exists to prevent (it removed the
    # `/etc/sudoers.d/docker-nopasswd` rule and made group membership a
    # consented step). The pattern requires the `(y/n)` suffix so it matches the
    # QUESTION and not the paragraph of warning the script prints above it.
    PromptRule(
        r"to the docker group.*\(y/n\)",
        ASK_THE_USER,
        "root-equivalent; neither answer is the app's to give",
    ),
    # Also above the catch-all, which answered both of these "y" until this
    # gate read the scripts line by line looking for what the button would say
    # on a machine unlike the test box (2026-08-23).
    #
    # Declined, not asked: `snap remove docker` takes away a working Docker the
    # user installed themselves, with every container and volume on it, and the
    # only reason the installer wants it gone is that IT cannot use snap
    # Docker. Saying no costs an exit with the script's own instruction to
    # remove it by hand ("Cannot continue with snap Docker"), which the 6.1
    # failure dialog now shows verbatim; saying yes costs data nobody agreed to
    # lose.
    #
    # Both carry the `(y/n)` suffix for the same reason the docker-group rule
    # does: `respond()` is handed every complete line, and on a quiet partial
    # line with no sudo marker it is handed the whole pending buffer. An
    # unanchored `search` therefore matches a reworded warning paragraph, a
    # summary echo, or a future script edit that prints the phrase for
    # information — writing "n" into a child that is not reading (which
    # desynchronises the next real prompt) or opening a modal dialog over
    # ordinary build output. No such line exists in today's scripts; the fix is
    # for the next rule someone adds by copying these (review, 2026-08-23).
    PromptRule(
        r"Remove snap Docker.*\(y/n\)",
        "n",
        "would remove the user's own Docker install; the script says how to do it by hand",
    ),
    # Asked, not decided: this one REBOOTS the machine ten seconds later
    # (`sudo systemctl reboot`, immutable Fedora / Bazzite path). The app
    # cannot know what else is open, and declining silently is no better — the
    # script then exits 0 having installed nothing. This is the second
    # `ASK_THE_USER` and the bar it clears is the same as the first's: an
    # exact question, printed as a whole line, whose two answers cost the user
    # different things that only they can weigh.
    PromptRule(
        r"Install Docker via rpm-ostree and reboot now.*\(y/n\)",
        ASK_THE_USER,
        "reboots the machine in 10s; neither answer is the app's to give",
    ),
    PromptRule(r"\(y/n\)", "y"),
)


def make_responder(
    options: InstallOptions,
    rules: tuple[PromptRule, ...] = PROMPT_RULES,
    ask: runner.Prompter | None = None,
) -> runner.Responder:
    """Build the `runner.Responder` that answers prompts per `rules` for `options`.

    `ask` is consulted only for a rule whose answer is `ASK_THE_USER` — two of
    them today, each pinned to its question by an unanchored `re.search`, not by
    the whole-line match this used to claim, for the reason given there. Without an `ask`
    (the CLI harness), such a prompt is DECLINED: refusing a privilege change is
    recoverable and visible, granting one silently is neither.
    """
    compiled = [(re.compile(r.pattern, re.IGNORECASE), r) for r in rules]

    def respond(line: str) -> str | None:
        for regex, rule in compiled:
            if not regex.search(line):
                continue
            if isinstance(rule.answer, AskTheUser):
                if ask is None:
                    logger.warning(f"no prompter for {line.strip()!r}; declining")
                    return "n"
                reply = ask(line.strip())
                # A dismissed dialog is not consent.
                answer = "y" if reply and reply.strip().lower() in ("y", "yes") else "n"
                logger.info(f"user was asked about {line.strip()!r} and answered {answer!r}")
                return answer
            answer = rule.answer(options) if callable(rule.answer) else rule.answer
            logger.debug(f"prompt {line.strip()!r} → {answer!r}")
            return answer
        return None

    return respond


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

    The recovery advice is split on the compose file, because one sentence was
    being used for two opposite situations and was wrong in the first. It used
    to say "Press Install again and choose {server_dir} to carry on", which
    walks a pre-build cancel straight back into the bug the cancel fix exists to
    remove: the script's line 961 finds no built worldserver image, takes the
    existing-folder branch, asks "Remove it and start fresh? (y/n):" — and
    `PROMPT_RULES` answers "n", because `InstallOptions.reinstall` is False and
    nothing in the GUI ever sets it. The script prints "Keeping existing install
    — exiting." and exits 0, which the view reads as a SUCCESS: it pins a
    compose project name into the half-cloned folder and remembers a server that
    does not exist. Roadmap 6.5 item 1 (a staged, resumable install) is unbuilt,
    so nothing here may promise resumption.

    After the build the same sentence is correct — 961 finds the images and
    genuinely skips the compile — and there Stop throws away work: a build the
    app now refuses to remember, with containers left running and no tab able to
    stop them. The app cannot tell the two apart at this moment without asking
    Docker, and asking is not safe enough to decide on: without a pin, compose
    derives the project from the folder's basename, so a second install in a
    same-named folder answers for this one (see `docker.install_project()`). So
    the copy names the evidence it does have — whether the source is on disk —
    and gives the action for each case, including "Use existing…", which needs
    only that compose file and was never mentioned (review, 2026-08-23).
    """
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
            f"{server_dir}, and the app will manage it from a tab — nothing is lost. If the "
            "build had not finished, pressing Install again will NOT carry on from where it "
            "stopped: the installer finds the folder, offers to wipe it, and the app declines, "
            f"so it exits having done nothing. Delete {server_dir} first in that case."
        )
    return (
        f"{lead} The installer had not got as far as writing a compose file "
        f"(compose.yml or docker-compose.yml), so there "
        "is nothing there for the app to manage and nothing to resume. Pressing Install again "
        f"will not pick up where it stopped — delete {server_dir} if it still exists, then "
        "start over."
    )


def host_package_manager() -> str | None:
    """The Linux package manager that picks the script variant; None off Linux."""
    if not sys.platform.startswith("linux"):
        return None
    return platform.linux_package_manager()


def bash_available(run: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> bool:
    """True if a `bash` that can actually run a script is on PATH.

    Being on PATH is not enough on Windows, for two different reasons measured
    on real machines:

    - On a Windows that has had WSL enabled at some point, `bash.exe` is the
      Store alias for WSL and fails with `execvpe(/bin/bash)` when no distro is
      installed. Docker Desktop's own WSL distros do not provide one.
    - On a genuinely clean Windows 11 (25H2, build 26200, measured 2026-08-22)
      there is no `bash.exe` at all — not in System32, not as an execution
      alias — so this returns False at the `which()` line and never runs
      anything.

    Both end at "no usable bash", which is why the probe runs the binary
    instead of trusting PATH. Note that `which()` alone is actively misleading
    on Windows for a different reason: `shutil.which("python")` returns a
    truthy path to a zero-byte Store alias on a machine with no Python at all,
    so any future interpreter probe needs this same shape.
    """
    if shutil.which("bash") is None:
        return False
    call = run if run is not None else runner.run
    try:
        return call(["bash", "-c", "exit 0"]).returncode == 0
    except OSError:
        return False


NO_BASH_HELP = (
    "The installers are shell scripts and this machine has no working `bash`. "
    "Install one (or repair the existing install), reopen the app, and try again."
)
# Deliberately platform-neutral: `preflight()` refuses on the platform gate
# BEFORE this check, so the old Windows/WSL advice was unreachable — and by
# roadmap 6.3 it is also wrong, since native Windows drives Docker Desktop's
# WSL2 backend rather than running the bash script in a distro.


# `docker_available()` used to live here as `runner.run(["docker", "info"])`,
# which is `platform.docker_ready()` written a second time (style-guide §4) —
# and the copy that never learned about `docker_programs()`. Deleting it rather
# than fixing it is what stops the pair drifting again: the preflight gate and
# the provisioning probe now agree by construction, so an install can no longer
# be refused with "Docker is not running" on a Windows box where
# `ensure_docker()` had just proved that it is.


class Installer:
    """Coordinate a full server install for a single catalog entry.

    Seams (`docker_check`, `ensure_docker`, `interact`) exist so the control
    flow is testable without Docker, a network, or a two-hour build.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        *,
        installers_root: Path = DEFAULT_INSTALLERS_ROOT,
        docker_check: Callable[[], bool] = platform.docker_ready,
        ensure_docker: Callable[..., platform.ProvisionReport] = platform.ensure_docker,
        interact: Callable[..., Iterator[str]] = runner.interact,
        env: Mapping[str, str] | None = None,
        package_manager: Callable[[], str | None] = host_package_manager,
        bash_check: Callable[[], bool] = bash_available,
        platform_id: Callable[[], str] = platform.detect,
    ) -> None:
        self.entry = entry
        self.installers_root = installers_root
        self._docker_check = docker_check
        self._ensure_docker = ensure_docker
        self._interact = interact
        self._env = env
        self._package_manager = package_manager
        self._bash_check = bash_check
        self._platform_id = platform_id
        # Per-install, so one install's marker cannot answer another's, and
        # random so no script output can imitate it. The wording around the
        # token matters too: this string is the LABEL of the one dialog in the
        # app that asks for the user's password, so it has to read as sudo
        # asking, not as a bare hex token (review, 2026-08-22).
        self.sudo_marker = f"{SUDO_PROMPT_PREFIX}{secrets.token_hex(8)}] password:"

    @property
    def script(self) -> Path:
        """Absolute path of the install script for this host.

        The catalog's `script` is the pacman/SteamOS one; `script_variants`
        names the Debian/Fedora ports (Phase 3 live-gate finding, 2026-08-20:
        on Ubuntu the default script would call `pacman`).
        """
        return self.installers_root / self.entry.install.script_for(self._package_manager())

    def script_env(self) -> dict[str, str]:
        """The environment the script runs in: ours, plus `env` overrides, a `TERM`, a sudo prompt.

        The scripts call `clear`/`tput`, which exit non-zero when `TERM` is unset
        — and a desktop-launched app has no `TERM` (Phase 3 live-gate finding,
        2026-08-20: `TERM environment variable not set.` → exit 1 before the
        first prompt). The ANSI output this enables is stripped by `runner`.

        `SUDO_PROMPT` is how the launcher recognises sudo's password prompt
        without guessing. sudo prints this string verbatim instead of "[sudo]
        password for pk:", so a marker containing a random token is proof the
        text came from sudo — no regex over build output, and no dependence on
        the user's locale, which "[sudo] password for" would have (a Danish box
        prints "[sudo] adgangskode for pk:").
        """
        env = dict(os.environ)
        if not env.get("TERM"):  # unset OR empty — some session managers export TERM=""
            env["TERM"] = DEFAULT_TERM
        # Everything above is a preference and `env` may override it. Everything
        # below is not.
        if self._env:
            env.update(self._env)
        # `SUDO_PROMPT` is a protocol identifier, not a setting: it is one half
        # of a matched pair with `ask_marker`, and letting a caller replace it
        # desynchronises the two, so the prompt is never recognised and the
        # install hangs with no dialog — the exact pre-6.1.5 failure
        # (review, 2026-08-22).
        env["SUDO_PROMPT"] = self.sudo_marker
        # The script now runs on a terminal, which re-arms every apt/dpkg path
        # that gates on isatty(): needrestart's service-restart menu and dpkg's
        # conffile prompt both render full-screen ncurses dialogs, neither
        # carries the marker, and no PROMPT_RULES entry answers them — so the
        # install would park on one with Stop as the only way out. Under the old
        # pipe transport those paths were non-interactive by accident; now they
        # are non-interactive on purpose (review, 2026-08-22).
        env.setdefault("DEBIAN_FRONTEND", "noninteractive")
        env.setdefault("NEEDRESTART_MODE", "a")
        env.setdefault("NEEDRESTART_SUSPEND", "yulon")
        return env

    def preflight(
        self,
        options: InstallOptions,
        cancel: threading.Event | None = None,
        *,
        ask: runner.Prompter | None = None,
    ) -> None:
        """Everything that must be true before a single line of the script runs.

        Raises `InstallerError` (script missing, client dir required but not
        given) or `DockerUnavailableError` — which no longer means "provisioning
        is unbuilt". This method CALLS `platform.ensure_docker()` when no daemon
        answers, and raises only when what came back cannot be used yet: a
        reboot is required, or the daemon still does not answer on a re-check.
        The report's manual steps ride the message (roadmap 3.3 → 5.1). `cancel`, when set, is
        passed through to Docker provisioning so its ready-poll can be
        interrupted (a stop mid-provision must not leave a worker sleeping).

        `ask` reaches Docker provisioning for two questions, in this order:
        whether to join the docker group, which is root-equivalent, and — on
        Linux, once a privileged step reports that it needs one — the sudo
        password (`platform.SudoSession`, 7.1). It has to arrive here rather
        than only in `run()`, because provisioning happens HERE — before the
        script starts. That ordering is why the scripts' own consent gate could
        never fire on the machine it was written for: `ensure_docker()` had
        already joined the group, so the script found the user a member and
        never asked (found 2026-08-24). This said "one question" until the sudo
        password landed beside it on another branch (merge review, 2026-08-31).
        """
        here = self._platform_id()
        if not self.entry.install.supports(here):
            # Before ANY subprocess: the script would fast-fail on its own
            # `[[ "$OSTYPE" == "linux-gnu"* ]]` gate and leave the user with a
            # bare "exited with status 1" (roadmap 6.1).
            raise UnsupportedPlatformError(unsupported_platform_message(self.entry, here))
        if not self.script.is_file():
            raise InstallerError(f"install script not found: {self.script}")
        if not self._bash_check():
            raise InstallerError(NO_BASH_HELP)
        if self.entry.install.requires_client_dir and options.client_dir is None:
            raise InstallerError(
                f"{self.entry.name} needs the folder of your {self.entry.client.version} "
                f"client (build {self.entry.client.build}) — pick it first; the app never "
                "downloads game clients"
            )
        if options.client_dir is not None and not options.client_dir.is_dir():
            raise InstallerError(f"client folder does not exist: {options.client_dir}")
        # Before Docker, because Docker provisioning is what asks for the
        # password. The scripts refuse this set themselves - `case "$SERVER_DIR"
        # in /|"$HOME"|/home|...` - but they refuse it AFTER their own sudo
        # prompt and after Docker discovery, so a folder that was never going to
        # work cost the user a password and a wait (live gate, 2026-08-25).
        # None means "let the script pick its own default", which is by
        # construction a dedicated subfolder and never one of these.
        if options.server_dir is not None:
            folder_problem = platform.server_dir_problem(options.server_dir)
            if folder_problem is not None:
                raise InstallerError(folder_problem)
        if not self._docker_check():
            report = self._ensure_docker(cancel=cancel, ask=ask)
            if report.reboot_required:
                raise DockerUnavailableError(
                    "Docker's prerequisites were installed but a reboot is needed first. "
                    + " ".join(report.manual_steps)
                )
            if not report.docker_ready and not self._docker_check():
                details = " ".join(report.manual_steps) or "; ".join(report.skipped)
                raise DockerUnavailableError(
                    "Docker isn't available and could not be set up automatically. "
                    + (details or "Install Docker, start it, and try again.")
                )

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: runner.Prompter | None = None,
    ) -> Iterator[str]:
        """Run the install, yielding output lines live; answers prompts itself.

        Once the script itself is running, `ask` is consulted for exactly one
        thing: `sudo` asking for a password during the distro package steps. No
        rule in `PROMPT_RULES` can ever know it, so without `ask` the script
        stops dead there — which is what installing on Linux did (`sudo -v` at
        the top of the Ubuntu script, guarded by `exit 1`, so it failed seconds
        in with "Could not cache sudo credentials. Aborting."). Before that,
        `preflight()` is handed the same `ask` and Docker provisioning may put
        its own two questions to it; see that method.

        Two things make that work, and both are needed:

        * The script runs on a pseudo-terminal. sudo reads its password from
          /dev/tty, not stdin, precisely so a piped stdin cannot feed it one —
          measured: a child reading stdin answers through a pipe, the same child
          reading /dev/tty does not.
        * `SUDO_PROMPT` (see `script_env()`) makes sudo announce itself with a
          random marker, so the prompt is recognised by an exact match instead
          of a guess about what a prompt looks like.

        Raises `InstallerError` if the script exits non-zero (after yielding
        everything it printed), or any `preflight()` error before it starts.
        Setting `cancel` interrupts the script (see `runner.interact()`).
        """
        opts = options or InstallOptions()
        self.preflight(opts, cancel=cancel, ask=ask)
        logger.info(f"installing {self.entry.id} via {self.script}")
        tail: deque[str] = deque(maxlen=_ERROR_TAIL_LINES)

        def stopped() -> bool:
            return cancel is not None and cancel.is_set()

        try:
            for line in self._interact(
                ["bash", str(self.script)],
                cwd=self.script.parent,
                # `ask` reaches the rules as well as `interact()`. The rules
                # need it for exactly one question — the docker-group consent
                # added by the installers' 1.4.4 security change, which arrives
                # as a COMPLETE line (the script `echo`s it, then reads), so
                # `interact()`'s blocked-partial-line path never sees it.
                respond=make_responder(opts, ask=ask),
                ask=ask,
                ask_marker=self.sudo_marker,
                env=self.script_env(),
                terminal=True,
                cancel=cancel,
            ):
                text = runner.strip_ansi(line).strip()
                if text:
                    tail.append(text)
                yield line
            if stopped():
                # `interact()` RETURNS on cancel rather than raising, so this
                # used to fall through to "install of wow-wotlk finished" — in
                # the app log, which is the file a user pastes into a bug
                # report, for an install they had just stopped 2.3 GB into a
                # clone (install gate, 2026-08-23).
                return
        except subprocess.CalledProcessError as exc:
            # Never just "exited with status N": the script's own last words are
            # the only thing that tells the user what went wrong (roadmap 6.1).
            said = "\n".join(tail)
            detail = f"\n\nIt last said:\n{said}" if said else ""
            raise InstallerError(
                f"{self.script.name} exited with status {exc.returncode}.{detail}"
            ) from exc
        finally:
            # In a `finally` because a cancel has two shapes and the other one
            # never reaches the line above. `_StreamWorker.run()` breaks its
            # loop on the first line that arrives AFTER Stop, and breaking drops
            # the last reference to this generator — so CPython closes it and
            # `GeneratorExit` is raised at the `yield`, which the `except` above
            # does not catch. That shape left the app log with no ending line at
            # all: "installing wow-wotlk via ..." and then nothing (review,
            # 2026-08-23).
            if stopped():
                logger.info(f"install of {self.entry.id} was cancelled")
        logger.info(f"install of {self.entry.id} finished")


class InstallEngine(Protocol):
    """What a catalog view can drive, whichever engine it got.

    Both `Installer` (the bash script) and every `native.StagedInstaller`
    family satisfy it, which is the whole reason `catalog_view.py`,
    `log_panel.py` and the job runner needed no changes for roadmap 6.2.
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
    """The engine that installs `entry` on THIS platform. The only place that decides.

    Script versus family, read from `catalog.json` rather than from what OS
    this is (style-guide §3, amendment A1): an entry with an `install.native`
    block is installed by its family engine on every platform it supports; an
    entry without one still runs its bash script, until 7.2 deletes that path
    (`Install.uses_script()`/`is_native()` are untouched here and go in 7.2).
    The platform refusal is unchanged from roadmap 6.1 and lives in each
    engine's `preflight()`, so an unsupported click is refused by whoever
    calls it.

    `import_probe`/`reset_unfinished` are per-game seams the CALLER supplies
    (`install_wiring.py`), because `catalog/` must not import a controller
    package. They are ignored on the script path, which runs its import
    through the script.

    Imported inside the function on purpose: `native.py` imports this module
    for `InstallOptions` and the error types, so naming it at module scope
    would be a cycle. The alternative — a fourth module holding three
    exceptions and a dataclass — buys nothing but an import.
    """
    from yulon.catalog import native
    from yulon.catalog.families import family_for

    if entry.install.native is None:
        return Installer(entry, installers_root=installers_root, platform_id=platform_id)
    return family_for(entry)(
        entry,
        installers_root=installers_root,
        import_probe=import_probe,
        reset_unfinished=reset_unfinished,
        seams=native.Seams(platform_id=platform_id),
    )


def _terminal_prompter(prompt: str) -> str:
    """Answer the prompts `run()` forwards, from the terminal.

    The CLI passed no `ask` at all, and `runner.interact()` writes nothing for
    a missing answer, so on any box where sudo wants a password the CLI parked
    at the prompt forever: no timeout, no error, a process that never exits.
    Reproduced on yulon-arch (2026-08-28), which is not passwordless.

    Never returns None. Off a terminal there is nothing to type, and an empty
    answer is the failure path that ENDS: sudo refuses it, retries, gives up,
    and the script's own guard exits non-zero with "Could not cache sudo
    credentials"; a y/n rule reads it as "no". A failure the user can read beats
    a hang they cannot.
    """
    import getpass
    import sys

    # Only sudo's own prompt is hidden. `ask` is consulted for EVERY ASK_THE_USER
    # rule, and the other two are consent questions - "Add '$USER' to the docker
    # group (grants root-equivalent access)?" and "Install Docker via rpm-ostree
    # and reboot now?" - which a person must be able to see themselves answering.
    # `script_env()` builds sudo's prompt with a random marker, so the two are
    # told apart exactly rather than guessed (review, 2026-08-28).
    if not sys.stdin.isatty():
        sys.stderr.write(f"no terminal to answer {prompt.strip()!r}; declining\n")
        return ""
    if SUDO_PROMPT_PREFIX in prompt:
        return getpass.getpass(prompt + " ")
    return input(prompt + " ")


def _main(argv: list[str] | None = None) -> int:
    """CLI entry point: `python -m yulon.catalog.installer <game-id> [options]`.

    The roadmap 3.2 test harness — streams the script's output to stdout and
    exits non-zero with the user-readable error on failure. Phase 4's
    `catalog_view.py` calls `Installer.run()` the same way.
    """
    import argparse
    import sys

    from yulon.catalog.catalog import load_catalog

    parser = argparse.ArgumentParser(prog="yulon.catalog.installer")
    parser.add_argument("game", help="catalog id, e.g. wow-wotlk")
    parser.add_argument("--server-dir", type=Path, default=None)
    parser.add_argument("--client-dir", type=Path, default=None)
    parser.add_argument("--reinstall", action="store_true")
    parser.add_argument("--installers-root", type=Path, default=DEFAULT_INSTALLERS_ROOT)
    args = parser.parse_args(argv)
    try:
        entry = load_catalog().get(args.game)
    except KeyError:
        sys.stderr.write(f"unknown game {args.game!r}\n")
        return 2
    # `installer_for()`, not `Installer(...)`: the CLI used to construct the
    # script engine directly, so it could never exercise a native family on
    # any platform, and "I ran the install through the CLI" proved less than it
    # sounded like it did. It now dispatches exactly as the Install button does.
    #
    # The import seams are wired the same way `main.py`'s `make_installer()`
    # wires them for the GUI - without this, a native install of an entry with
    # an `import_service` (WoW WotLK) refuses at preflight with "this installer
    # was built without a way to check it", on every platform, before a single
    # container is created. Local imports for the same reason `native.py`
    # imports `installer` inside its own function: `catalog/` must not import a
    # controller package at module scope.
    import_probe = None
    reset_unfinished = None
    spec = entry.container_spec()
    if spec.import_service:
        from yulon.apply import DockerSql
        from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
        from yulon.controller_wow_wotlk import modules as wotlk_modules
        from yulon.controller_wow_wotlk import repair as wotlk_repair

        password = entry.install.password.value or wotlk_modules.DEFAULT_DB_ROOT_PASSWORD
        sql = DockerSql(spec.db, password, schemas=entry.schema_map())
        mysql = wotlk_maintenance.DockerMysql(spec.db, password)
        import_probe = lambda: wotlk_repair.import_state(sql, mysql)  # noqa: E731
        reset_unfinished = lambda: wotlk_repair.reset_unfinished(sql, mysql)  # noqa: E731
    installer = installer_for(
        entry,
        installers_root=args.installers_root,
        import_probe=import_probe,
        reset_unfinished=reset_unfinished,
    )
    options = InstallOptions(
        server_dir=args.server_dir, client_dir=args.client_dir, reinstall=args.reinstall
    )
    try:
        for line in installer.run(options, ask=_terminal_prompter):
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except InstallerError as exc:
        sys.stderr.write(f"install failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
