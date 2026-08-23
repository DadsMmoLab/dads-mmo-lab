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
# How many of the script's last output lines a failure message carries (roadmap 6.1).
_ERROR_TAIL_LINES = 12
# What the scripts see as their terminal when the app was not started from one.
DEFAULT_TERM = "xterm-256color"


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

There is exactly one of these, and it is not a general escape hatch: a rule that
opens a dialog is the shape that made the old prompt heuristic dangerous. It
exists because the installers' docker-group question has no safe canned answer.
Answering yes grants root-equivalent access silently — the precise thing
upstream's 1.4.4 security change added consent for. Answering no leaves the user
outside the docker group, and the launcher's own `docker` calls then fail with
permission denied, so the app would have quietly broken itself instead.
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
    PromptRule(r"\(y/n\)", "y"),
)


def make_responder(
    options: InstallOptions,
    rules: tuple[PromptRule, ...] = PROMPT_RULES,
    ask: runner.Prompter | None = None,
) -> runner.Responder:
    """Build the `runner.Responder` that answers prompts per `rules` for `options`.

    `ask` is consulted only for a rule whose answer is `ASK_THE_USER` — one rule,
    matching one exact question, for the reason given there. Without an `ask`
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
        self.sudo_marker = f"[sudo via Yu'lon {secrets.token_hex(8)}] password:"

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

    def preflight(self, options: InstallOptions, cancel: threading.Event | None = None) -> None:
        """Everything that must be true before a single line of the script runs.

        Raises `InstallerError` (script missing, client dir required but not
        given) or `DockerUnavailableError` (no daemon and provisioning not yet
        implemented — roadmap 3.3's graceful failure). `cancel`, when set, is
        passed through to Docker provisioning so its ready-poll can be
        interrupted (a stop mid-provision must not leave a worker sleeping).
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
        if not self._docker_check():
            report = self._ensure_docker(cancel=cancel)
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

        `ask` is consulted for exactly one thing: `sudo` asking for a password
        during the distro package steps. No rule in `PROMPT_RULES` can ever know
        it, so without `ask` the script stops dead there — which is what
        installing on Linux did (`sudo -v` at the top of the Ubuntu script,
        guarded by `exit 1`, so it failed seconds in with "Could not cache sudo
        credentials. Aborting.").

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
        self.preflight(opts, cancel=cancel)
        logger.info(f"installing {self.entry.id} via {self.script}")
        tail: deque[str] = deque(maxlen=_ERROR_TAIL_LINES)
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
        except subprocess.CalledProcessError as exc:
            # Never just "exited with status N": the script's own last words are
            # the only thing that tells the user what went wrong (roadmap 6.1).
            said = "\n".join(tail)
            detail = f"\n\nIt last said:\n{said}" if said else ""
            raise InstallerError(
                f"{self.script.name} exited with status {exc.returncode}.{detail}"
            ) from exc
        logger.info(f"install of {self.entry.id} finished")


class InstallEngine(Protocol):
    """What a catalog view can drive, whichever engine it got.

    Both `Installer` (the bash script) and `native.NativeInstaller` satisfy it,
    which is the whole reason `catalog_view.py`, `log_panel.py` and the job
    runner needed no changes for roadmap 6.2.
    """

    def preflight(self, options: InstallOptions, cancel: threading.Event | None = None) -> None: ...

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

    Three rules, in order, all of them reading `catalog.json` rather than
    asking what OS this is (style-guide §3):

    1. the platform is not in `install.platforms` — refused by whoever calls
       `preflight()`, unchanged from roadmap 6.1. A `Installer` is returned so
       that refusal comes from the one place that words it;
    2. the platform is in `install.script_platforms` — today's script path,
       byte for byte what Linux already runs;
    3. otherwise — the native engine.

    `import_probe`/`reset_unfinished` are per-game seams the CALLER supplies
    (the app wires `controller_wow_wotlk.repair`), because `catalog/` must not
    import a controller package. They are ignored on the script path, which
    runs its import through the script.

    Imported inside the function on purpose: `native.py` imports this module
    for `InstallOptions` and the error types, so naming it at module scope
    would be a cycle. The alternative — a fourth module holding three
    exceptions and a dataclass — buys nothing but an import.
    """
    from yulon.catalog import native

    here = platform_id()
    if entry.install.is_native(here):
        return native.NativeInstaller(
            entry,
            installers_root=installers_root,
            import_probe=import_probe,
            reset_unfinished=reset_unfinished,
            seams=native.Seams(platform_id=platform_id),
        )
    return Installer(entry, installers_root=installers_root, platform_id=platform_id)


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
    installer = Installer(entry, installers_root=args.installers_root)
    options = InstallOptions(
        server_dir=args.server_dir, client_dir=args.client_dir, reinstall=args.reinstall
    )
    try:
        for line in installer.run(options):
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except InstallerError as exc:
        sys.stderr.write(f"install failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
