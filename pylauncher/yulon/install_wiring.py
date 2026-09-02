"""Wiring an install engine for the app: the per-game seams `catalog/` may not build itself.

`catalog/` must not import a `controller_*` package, and `repair.py` is what
knows the `acore_*` schema names that answer "does this install's database look
imported?". So the probe pair is assembled HERE and handed down — once, for
`main.py`'s Catalog tab, `ui/controller_view.py`'s Server tab and the CLI
harness alike. Before this module each of the first two hand-wrote the same
lambdas and the same fixed-password fallback, and the harness in
`catalog/installer.py` wrote a third copy behind a lazy import that broke the
layering rule it was under.

Also the CLI harness (`python -m yulon.install_wiring <game> [--server-dir]
[--client-dir]`), moved from `catalog/installer.py` for the same reason: it
must wire the probe, and it could not from inside `catalog/`.

No UI here (style-guide §3): this module knows entries, engines and seams. The
one thing that looks like UI, `_terminal_prompter`, is the harness's own stdio
and never reaches the GUI, which passes `ui/widgets/prompt.py` instead.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable
from pathlib import Path

from yulon import docker, platform
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import (
    DEFAULT_INSTALLERS_ROOT,
    SUDO_PROMPT_PREFIX,
    InstallEngine,
    InstallerError,
    InstallOptions,
    installer_for,
)
from yulon.log import get_logger, use_utf8_streams

logger = get_logger(__name__)

DEFAULT_DB_ROOT_PASSWORD = "password"
"""acore-docker's default root password: what every fixed-password WotLK install shares with
backup, the console and every archived guide, and the fallback when an entry's plan has no
fixed value (moved from `controller_wow_wotlk.modules`, which re-exports it)."""


def fixed_db_password(entry: CatalogEntry) -> str:
    """The password the app's own DB clients use for `entry`: its fixed value, else the default.

    A `generated` plan has no value, and the fallback is the one both callers
    already carried. That is correct for every consumer this module has — the
    AzerothCore probe pair — because only a fixed-password entry names a
    one-shot import service. It is NOT a general "what is this install's
    password": a caller that holds a `server_dir` must ask
    `entry.install.db_password(server_dir)`, which reads the file a generated
    plan wrote, and 7.3's `MarkerGate` reads that same file.
    """
    return entry.install.password.value or DEFAULT_DB_ROOT_PASSWORD


def import_gate_for(
    entry: CatalogEntry,
    *,
    wsl_distro: str | None = None,
) -> tuple[docker.ImportProbe | None, docker.ResetUnfinished | None]:
    """The AzerothCore probe/reset pair for `entry`, or `(None, None)`.

    Only for a game that names a one-shot import service: `repair.import_state()`
    looks for the `acore_*` schemas by name, so attaching it to every entry told
    a healthy CMaNGOS install its databases were never imported and offered it
    the destructive Repair button (review, 2026-08-23). `import_service` is the
    same fact `repair_import()` refuses on, so the two agree by construction.

    Both seams, because neither answers the whole question alone: `DockerMysql`
    can list schemas without naming one to connect to, `DockerSql` reads inside
    them. Both bind to THIS entry's db container, never `docker_ctl.SPEC.db`.

    Two facts travel with them that a bare `(container, password)` pair loses.
    `schemas=entry.schema_map()` is what keeps a CMaNGOS install off
    AzerothCore's `acore_*` names — this function holds both the entry and the
    seam, so it is one of the few places that can say which schemas exist here.
    `wsl_distro` is the other half of that sentence: the schemas say WHICH
    databases, the distro says which daemon they are inside, and without it a
    WSL-resident server's `docker exec` goes to the Windows-local daemon that
    has never heard of `ac-database`. It defaults to None because the Catalog
    tab and the CLI install locally; the Server tab knows the distro of an
    existing install and passes it.

    The password is deliberately the FIXED one and not a file read: this
    function has no `server_dir`, and it is only ever built for an entry that
    names an import service — wow-wotlk, whose plan is `fixed`. A game that
    generates its password can never reach the line.

    `apply` and the two controller modules are imported inside the function on
    purpose: `controller_wow_wotlk.modules` imports THIS module at module scope
    for `DEFAULT_DB_ROOT_PASSWORD`, so naming its package up top would close the
    import graph on itself the first time anything in that package moves.
    """
    spec = entry.container_spec()
    if not spec.import_service:
        return None, None

    from yulon.apply import DockerSql
    from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
    from yulon.controller_wow_wotlk import repair as wotlk_repair

    password = fixed_db_password(entry)
    sql = DockerSql(spec.db, password, schemas=entry.schema_map(), wsl_distro=wsl_distro)
    mysql = wotlk_maintenance.DockerMysql(spec.db, password, wsl_distro=wsl_distro)

    def probe() -> docker.ImportState:
        return wotlk_repair.import_state(sql, mysql)

    def reset() -> tuple[str, ...]:
        return wotlk_repair.reset_unfinished(sql, mysql)

    return probe, reset


def installer_for_app(
    entry: CatalogEntry,
    *,
    platform_id: Callable[[], str] = platform.detect,
    installers_root: Path = DEFAULT_INSTALLERS_ROOT,
) -> InstallEngine:
    """The engine the app drives for `entry`: `installer_for()` plus this game's import gate.

    No `wsl_distro`: an install creates the server here, on whatever daemon
    `docker` reaches from this process. Only an EXISTING install can live in a
    distro the app has to name, and that is the Server tab's question.
    """
    probe, reset = import_gate_for(entry)
    return installer_for(
        entry,
        platform_id=platform_id,
        installers_root=installers_root,
        import_probe=probe,
        reset_unfinished=reset,
    )


def _terminal_prompter(prompt: str) -> str:
    """Answer the prompts `run()` forwards, from the terminal.

    The CLI passed no `ask` at all until 2026-08-28, and `runner.interact()`
    writes nothing for a missing answer, so on any box where sudo wants a
    password the CLI parked at the prompt forever: no timeout, no error, a
    process that never exits. Reproduced on yulon-arch, which is not
    passwordless.

    Never returns None. Off a terminal there is nothing to type, and an empty
    answer is the failure path that ENDS: sudo refuses it, retries, gives up,
    and the caller's own guard exits non-zero; a y/n rule reads it as "no". A
    failure the user can read beats a hang they cannot.

    Only a password question is hidden. `ask` is consulted for every question
    an install can raise, and the others are consent questions — "Add '$USER'
    to the docker group (grants root-equivalent access)?", "Install Docker via
    rpm-ostree and reboot now?" — which a person must be able to see themselves
    answering. Two spellings are secret, not one, and both are still matched.
    The bash engine set `SUDO_PROMPT` to a random marker behind
    `SUDO_PROMPT_PREFIX`; 7.2 deleted that engine, so nothing writes that
    spelling today and only the native path's
    `platform.SUDO_PASSWORD_QUESTION`, asked through `SudoSession`, arrives
    here. The prefix is kept rather than dropped because it costs one `in` test
    and the failure it guards against is a root password echoed to the terminal
    as it is typed — which is what the harness this replaced did on the native
    path, knowing only the first spelling.
    """
    if not sys.stdin.isatty():
        sys.stderr.write(f"no terminal to answer {prompt.strip()!r}; declining\n")
        return ""
    if SUDO_PROMPT_PREFIX in prompt or platform.SUDO_PASSWORD_QUESTION in prompt:
        return getpass.getpass(prompt + " ")
    return input(prompt + " ")


def main(argv: list[str] | None = None) -> int:
    """CLI harness: `python -m yulon.install_wiring <game-id> [--server-dir] [--client-dir]`.

    Streams the engine's lines to stdout and exits 1 with the user-readable
    error on failure — an engine that cannot be built for this game included,
    which since 7.2 is what `installer_for()` raises for an entry with no
    `install.native` block — and 2 for an unknown game. Building the engine is
    INSIDE the `try` for that reason (A1): a refusal that escaped it left the
    harness printing a traceback instead of the sentence written for a person.
    The Catalog tab drives the same `run()`; this is how a gate is run from a
    terminal on a test VM. There is no `--reinstall`: the harness never
    re-answered the existing-server-dir question, and 7.2 deleted
    `InstallOptions.reinstall` along with the rule that read it.

    `--installers-root` is not in the contract's CLI spelling and is not a
    product surface: it lets a gate point the engine at a checkout's templates
    instead of a packaged bundle's.
    """
    # Before ANY output. Windows hands a redirected stream cp1252, and this
    # harness prints whatever the engine yields -- which includes real arrows.
    # Measured on yulon-win11 2026-09-03: the install died at the end of
    # preflight on `→`, so no Windows gate could reach a stage at all.
    use_utf8_streams()
    parser = argparse.ArgumentParser(prog="yulon.install_wiring")
    parser.add_argument("game", help="catalog id, e.g. wow-wotlk")
    parser.add_argument("--server-dir", type=Path, default=None)
    parser.add_argument("--client-dir", type=Path, default=None)
    parser.add_argument("--installers-root", type=Path, default=DEFAULT_INSTALLERS_ROOT)
    args = parser.parse_args(argv)
    try:
        entry = load_catalog().get(args.game)
    except KeyError:
        sys.stderr.write(f"unknown game {args.game!r}\n")
        return 2
    options = InstallOptions(server_dir=args.server_dir, client_dir=args.client_dir)
    try:
        engine = installer_for_app(entry, installers_root=args.installers_root)
        # `ask=` is not optional. Provisioning asks the docker-group consent and
        # the sudo password through it, and a run given no prompter answers
        # neither — which on a password-sudo box is the hang above.
        for line in engine.run(options, ask=_terminal_prompter):
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except InstallerError as exc:
        logger.error(f"install failed: {exc}")
        sys.stderr.write(f"install failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
