"""Install orchestrator (Phase 3a): drive one catalog entry's existing `install-*.sh`.

Phase 3a wraps the scripts we already ship instead of reimplementing them
(README §7/§9): the orchestrator resolves the entry's script, answers the
script's prompts from a typed rule table so no shell interaction is needed,
and streams the output up to whoever is listening (a CLI today, the
`log_panel` in Phase 4). It never downloads client assets — the user's own
client directory is *passed in* (README §3a) — and it never contains
per-game logic: what differs per game is `catalog.json` data.

Docker provisioning is wired in (roadmap 3.3 → 5.1): if no daemon answers,
`platform.ensure_docker()` is asked to provide one; when it cannot (needs a
reboot, a password, a manual install) that is a clean, logged, catchable
`DockerUnavailableError` carrying the report's manual steps — never a crash
or a silent hang.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, resources, runner
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

# Where `archive/guides/...` resolves from: the repo root, or the bundle when frozen.
DEFAULT_REPO_ROOT = resources.repo_root()
# What the scripts see as their terminal when the app was not started from one.
DEFAULT_TERM = "xterm-256color"


class InstallerError(RuntimeError):
    """The install could not start or did not finish (message is user-readable)."""


class DockerUnavailableError(InstallerError):
    """No Docker daemon is reachable and automatic provisioning is not available yet."""


@dataclass(frozen=True)
class InstallOptions:
    """What the user decided before clicking install."""

    server_dir: Path | None = None
    client_dir: Path | None = None
    reinstall: bool = False


@dataclass(frozen=True)
class PromptRule:
    """`pattern` (regex, searched in the ANSI-stripped prompt) → the stdin answer."""

    pattern: str
    answer: str | Callable[[InstallOptions], str]
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
    PromptRule(r"\(y/n\)", "y"),
)


def make_responder(
    options: InstallOptions, rules: tuple[PromptRule, ...] = PROMPT_RULES
) -> runner.Responder:
    """Build the `runner.Responder` that answers prompts per `rules` for `options`."""
    compiled = [(re.compile(r.pattern, re.IGNORECASE), r) for r in rules]

    def respond(line: str) -> str | None:
        for regex, rule in compiled:
            if regex.search(line):
                answer = rule.answer(options) if callable(rule.answer) else rule.answer
                logger.debug(f"prompt {line.strip()!r} → {answer!r}")
                return answer
        return None

    return respond


def host_package_manager() -> str | None:
    """The Linux package manager that picks the script variant; None off Linux."""
    if not sys.platform.startswith("linux"):
        return None
    return platform.linux_package_manager()


def docker_available() -> bool:
    """True if `docker info` succeeds; False if the binary or daemon is missing."""
    try:
        return runner.run(["docker", "info"]).returncode == 0
    except OSError:
        return False


class Installer:
    """Coordinate a full server install for a single catalog entry.

    Seams (`docker_check`, `ensure_docker`, `interact`) exist so the control
    flow is testable without Docker, a network, or a two-hour build.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        *,
        repo_root: Path = DEFAULT_REPO_ROOT,
        docker_check: Callable[[], bool] = docker_available,
        ensure_docker: Callable[[], platform.ProvisionReport] = platform.ensure_docker,
        interact: Callable[..., Iterator[str]] = runner.interact,
        env: Mapping[str, str] | None = None,
        package_manager: Callable[[], str | None] = host_package_manager,
    ) -> None:
        self.entry = entry
        self.repo_root = repo_root
        self._docker_check = docker_check
        self._ensure_docker = ensure_docker
        self._interact = interact
        self._env = env
        self._package_manager = package_manager

    @property
    def script(self) -> Path:
        """Absolute path of the install script for this host.

        The catalog's `script` is the pacman/SteamOS one; `script_variants`
        names the Debian/Fedora ports (Phase 3 live-gate finding, 2026-08-20:
        on Ubuntu the default script would call `pacman`).
        """
        return self.repo_root / self.entry.install.script_for(self._package_manager())

    def script_env(self) -> dict[str, str]:
        """The environment the script runs in: ours, plus `env` overrides, plus a `TERM`.

        The scripts call `clear`/`tput`, which exit non-zero when `TERM` is unset
        — and a desktop-launched app has no `TERM` (Phase 3 live-gate finding,
        2026-08-20: `TERM environment variable not set.` → exit 1 before the
        first prompt). The ANSI output this enables is stripped by `runner`.
        """
        env = dict(os.environ)
        env.setdefault("TERM", DEFAULT_TERM)
        if self._env:
            env.update(self._env)
        return env

    def preflight(self, options: InstallOptions) -> None:
        """Everything that must be true before a single line of the script runs.

        Raises `InstallerError` (script missing, client dir required but not
        given) or `DockerUnavailableError` (no daemon and provisioning not yet
        implemented — roadmap 3.3's graceful failure).
        """
        if not self.script.is_file():
            raise InstallerError(f"install script not found: {self.script}")
        if self.entry.install.requires_client_dir and options.client_dir is None:
            raise InstallerError(
                f"{self.entry.name} needs the folder of your {self.entry.client.version} "
                f"client (build {self.entry.client.build}) — pick it first; the app never "
                "downloads game clients"
            )
        if options.client_dir is not None and not options.client_dir.is_dir():
            raise InstallerError(f"client folder does not exist: {options.client_dir}")
        if not self._docker_check():
            report = self._ensure_docker()
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

    def run(self, options: InstallOptions | None = None) -> Iterator[str]:
        """Run the install, yielding output lines live; answers prompts itself.

        Raises `InstallerError` if the script exits non-zero (after yielding
        everything it printed), or any `preflight()` error before it starts.
        """
        opts = options or InstallOptions()
        self.preflight(opts)
        logger.info(f"installing {self.entry.id} via {self.script}")
        try:
            yield from self._interact(
                ["bash", str(self.script)],
                cwd=self.script.parent,
                respond=make_responder(opts),
                env=self.script_env(),
            )
        except subprocess.CalledProcessError as exc:
            raise InstallerError(f"{self.script.name} exited with status {exc.returncode}") from exc
        logger.info(f"install of {self.entry.id} finished")


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
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        entry = load_catalog().get(args.game)
    except KeyError:
        sys.stderr.write(f"unknown game {args.game!r}\n")
        return 2
    installer = Installer(entry, repo_root=args.repo_root)
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
