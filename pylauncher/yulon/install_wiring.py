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
import logging
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from yulon import docker, platform, wsl
from yulon.catalog import upstream

# By name and not as the module. `import_gate_for()` below binds a local called
# `native` in a walrus (`entry.install.native`), and a module of the same name
# imported at the top of the file is exactly the kind of shadow that type-checks
# in one function and not in the other.
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.installer import (
    DEFAULT_INSTALLERS_ROOT,
    SUDO_PROMPT_PREFIX,
    InstallEngine,
    InstallerError,
    InstallOptions,
    installer_for,
)
from yulon.catalog.native import (
    ComposeRepairRoute,
    LatestRoute,
    RewrittenHistory,
    Seams,
    SourceVersion,
    read_state,
    return_to_pin_confirmation,
    rewritten_line,
    source_version,
    update_to_latest_confirmation,
)
from yulon.log import configure, get_logger, use_utf8_streams
from yulon.ui import lines

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
    # `client=` on BOTH. This function is entry-driven -- it builds the import
    # gate for whichever game is being installed, not for WotLK -- so an
    # unbound seam here falls back to `mysql` on the three CMaNGOS games, which
    # run MariaDB and ship no such binary, whenever the container cannot be
    # probed. Found by an audit of every construction site after the same
    # defect had been fixed in the controller packages and then again in the UI
    # (review, 2026-09-03).
    client = native.db.client if (native := entry.install.native) is not None else None
    sql = DockerSql(
        spec.db, password, schemas=entry.schema_map(), wsl_distro=wsl_distro, client=client
    )
    mysql = wotlk_maintenance.DockerMysql(spec.db, password, wsl_distro=wsl_distro, client=client)

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
    wsl_distro: str | None = None,
) -> InstallEngine:
    """The engine the app drives for `entry`: `installer_for()` plus this game's import gate.

    `wsl_distro` is for an EXISTING install that lives inside a distro -- the
    Server tab's Rebuild and Update to latest (T125) -- and builds the engine on
    `Seams.in_wsl()`, whose every docker and git question goes to that distro's
    Docker. An install never passes one: it creates the server on whatever
    daemon this process reaches, and Yu'lon does not install into a distro
    (`pyplan/wsl-resident-servers.md` §7).
    """
    probe, reset = import_gate_for(entry, wsl_distro=wsl_distro)
    if wsl_distro is None:
        return installer_for(
            entry,
            platform_id=platform_id,
            installers_root=installers_root,
            import_probe=probe,
            reset_unfinished=reset,
        )
    return installer_for(
        entry,
        installers_root=installers_root,
        import_probe=probe,
        reset_unfinished=reset,
        seams=Seams.in_wsl(wsl_distro),
    )


def _refuse_unless_in_the_distro(server_dir: Path, wsl_distro: str) -> None:
    """The folder must be a WSL folder IN `wsl_distro`; refused before anything is read or run.

    `platform.wsl_linux_path_in()` keeps the distro the UNC path names and
    compares it with the one the install is remembered under, so a stale name
    cannot send a rebuild to the same Linux path in another distro (a clone of
    it, say). The image names come from the install's own record
    (`native.recorded_install_id()`), read only after this has passed.
    """
    try:
        inside = platform.wsl_linux_path_in(server_dir, wsl_distro)
    except platform.WslDistroMismatch as exc:
        raise InstallerError(str(exc)) from exc
    if inside is None:
        raise InstallerError(
            f"{server_dir} is not a folder inside the WSL distro {wsl_distro}, so there is no "
            "server there to rebuild. Nothing was started."
        )


def _in_the_distro(server_dir: Path, wsl_distro: str | None) -> bool:
    """`_refuse_unless_in_the_distro()` as a yes/no, for a READING, which must not raise."""
    if wsl_distro is None:
        return True
    try:
        _refuse_unless_in_the_distro(server_dir, wsl_distro)
    except InstallerError as exc:
        logger.warning(f"not reading {server_dir}: {exc}")
        return False
    return True


def _distro_down(wsl_distro: str | None) -> bool:
    """True when this install's distro is not running, so a READING must not touch it.

    `wsl.is_running()` reads WSL's own listing, which starts nothing; asking the
    folder (`\\\\wsl.localhost\\...`) or its Docker would boot the distro
    (`pyplan/wsl-resident-servers.md` §2). A local install is never down here.
    """
    return wsl_distro is not None and not wsl.is_running(wsl_distro)


RebuildSource = Callable[[threading.Event | None], Iterator[str]]
"""What a controller tab drives to rebuild its install: cancel in, lines out.

Deliberately not the `InstallEngine` itself. The tab holds a server dir and a
distro and nothing else; handing it an engine would make it responsible for
`InstallOptions`, for the factory and for the WSL question below, none of which
is a view's business (style-guide §3).
"""


def rebuild_for_app(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
) -> RebuildSource:
    """The rebuild a controller tab presses, wired for the install at `server_dir`.

    The engine is built PER PRESS, inside the generator, not here. Building it
    eagerly would run `import_gate_for()` and construct four seams for every tab
    the app opens, for a button most of them will never be pressed; building it
    on the press also means a rebuild after a Docker reinstall gets a fresh
    engine rather than one bound at startup.

    **A server living inside a WSL distro is rebuilt THERE (T125).** Until
    T125 this refused, and the refusal was right for the seams it had: they
    addressed the local daemon, so a rebuild would have compiled on Windows'
    own Docker and left the distro's server running the build it already had.
    `installer_for_app(wsl_distro=)` now builds the engine on
    `Seams.in_wsl()`, which addresses the distro's Docker for every docker and
    git question. Its images are named after the install id the folder's
    record carries (`native.recorded_install_id()`), after the folder is
    checked to be in that distro at all (`_refuse_unless_in_the_distro()`).
    """

    def rebuild(cancel: threading.Event | None = None) -> Iterator[str]:
        if wsl_distro is not None:
            _refuse_unless_in_the_distro(server_dir, wsl_distro)
        engine = installer_for_app(entry, wsl_distro=wsl_distro)
        yield from engine.rebuild(InstallOptions(server_dir=server_dir), cancel=cancel)

    return rebuild


def update_to_latest_for_app(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
) -> LatestRoute | None:
    """The "Update the server to latest…" control for this install, or None when it has none.

    Two reasons to answer None, and they are different facts — `controller_view.
    _updates_route()`'s division, applied to T64's control:

    * **The entry does not offer it.** `install.native.update_to_latest`, read
      off the catalog rather than off an id, so an entry that gains the flag
      gains the control with no code change here. An entry with no `native`
      block at all has no engine either and is the same answer.

    A server inside a WSL distro is offered it too since T125, on the engine
    `installer_for_app(wsl_distro=)` builds there. Its two READINGS --
    `source_version` on every tab reload, `upstream_news` once a day -- answer
    "nothing to say" while the distro is stopped rather than read the folder
    or ask its git, either of which would boot it (wsl-resident-servers §2).

    The engine is built inside each callable, per press, for `rebuild_for_app()`'s
    reason — except `source_version`, which is called on every tab reload and
    therefore builds nothing at all: it reads one small JSON file. `valid=()` is
    what says so, and `catalog.native._parse_state()` documents it as "the caller is not
    asking about stages", which this one is not.
    """
    block = entry.install.native
    if block is None or not block.update_to_latest:
        return None
    options = InstallOptions(server_dir=server_dir)
    moving = installer_for(entry).sources_that_move()
    # The repository the dialog names. The FIRST source that moves is the core
    # in every shipped entry, and it is the one whose sha a user would paste
    # into an issue. Read through the engine rather than as `sources[0]` so the
    # dialog can never name a database repository this press does not touch.
    repo = moving[0].repo if moving else entry.emulator.sources[0].repo

    # T126: a release on history upstream rewrote moves only when the question
    # the player answered said so. What the question said is kept here, between
    # `confirmation()` and `press()`, and a press that met a divergence the
    # question did not describe (the day's count was stale or missing) is
    # remembered, so the next question carries its line.
    acknowledged: dict[str, str] = {}
    met: dict[str, str] = {}

    def rewritten() -> dict[str, str]:
        # A file read, no network and no engine: this runs on the GUI thread,
        # inside the press, before the question is shown.
        keys = [(source.repo, source.follow) for source in moving]
        rows = upstream.read_cached(server_dir, keys, upstream.now_unix()).values()
        found = {
            row.repo: rewritten_line(row.repo, row.release, row.rewritten)
            for row in rows
            if row.rewritten and row.release
        }
        return {**found, **met}

    def confirmation() -> str:
        said = rewritten()
        acknowledged.clear()
        acknowledged.update(said)
        return update_to_latest_confirmation(entry, server_dir, repo, tuple(said.values()))

    def engine() -> InstallEngine:
        if wsl_distro is not None:
            _refuse_unless_in_the_distro(server_dir, wsl_distro)
        return installer_for_app(entry, wsl_distro=wsl_distro)

    def press(cancel: threading.Event | None = None) -> Iterator[str]:
        try:
            yield from engine().update_to_latest(
                options, cancel=cancel, rewritten_ok=frozenset(acknowledged)
            )
        except RewrittenHistory as exc:
            met[exc.repo] = exc.line
            raise
        met.clear()

    def to_pin(cancel: threading.Event | None = None) -> Iterator[str]:
        yield from engine().update_to_latest(options, to_pin=True, cancel=cancel)

    def version() -> SourceVersion:
        if not _in_the_distro(server_dir, wsl_distro) or _distro_down(wsl_distro):
            return SourceVersion(line="", past_the_pin=False)
        return source_version(read_state(server_dir, valid=()))

    def news() -> upstream.UpstreamNews:
        # T124. Built per call like the presses: it is asked off the GUI thread
        # and at most once a day reaches past its cache. No import gate: it asks
        # nothing of the databases.
        if not _in_the_distro(server_dir, wsl_distro) or _distro_down(wsl_distro):
            # "Could not ask", which the tab shows as nothing: no cache read (a
            # read of the folder boots the distro as well) and no git.
            return upstream.UpstreamNews(checked_unix=upstream.now_unix(), sources=())
        if wsl_distro is None:
            return installer_for(entry).upstream_news(options)
        return installer_for(entry, seams=Seams.in_wsl(wsl_distro)).upstream_news(options)

    return LatestRoute(
        confirmation=confirmation,
        press=press,
        pin_confirmation=lambda: return_to_pin_confirmation(entry, server_dir, repo),
        to_pin=to_pin,
        source_version=version,
        upstream_news=news,
    )


def repair_compose_for_app(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
) -> ComposeRepairRoute | None:
    """T106's "Repair server files…" for this install, or None when it has none.

    Offered to the CMaNGOS family (TBC, Vanilla, Tortoise) and nothing else, read
    off `install.native.family`. Those installs keep the `docker-compose.yml`
    they were installed with: `rebuild_stages()` leaves generate-compose out on
    purpose, and Update-to-latest rewrites a compose file only for a source whose
    `dest` is the server dir (`app_written_paths()`), which is WotLK's alone. So
    WotLK is not offered it -- its base file already follows the app on Update,
    and its override is the Tuning tab's (T94/T101) -- and a family added later
    is not offered it until somebody decides it should be.

    None as well for a server inside a WSL distro, for `rebuild_for_app()`'s
    reason: the engine's seams address this host, so the folder it would render
    for is not the one the containers were made from.

    The engine is built per call, for `rebuild_for_app()`'s reason.
    """
    if wsl_distro is not None:
        return None
    block = entry.install.native
    if block is None or block.family != "cmangos":
        return None
    options = InstallOptions(server_dir=server_dir)
    return ComposeRepairRoute(
        check=lambda: installer_for_app(entry).base_compose_check(options),
        repair=lambda: installer_for_app(entry).repair_base_compose(options),
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

    Writes the same `yulon.log` as `main.py`, into the same `config_dir()`.
    What holds them to that is a test that RUNS every module this app can be
    started as — three of them since `python -m yulon` became a driven row on
    2026-09-05, not the two this sentence used to name — and fails on the
    disagreement rather than on either missing call; see the `configure()`
    call below.
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
    # The same `yulon.log` the GUI writes, for the entry point that needs it
    # more. Measured on `yulon-ubuntu` 2026-09-05: after the entire 7.2 gate —
    # every step of it driven through this harness — `~/.local/share/yulon/`
    # did not exist, because `configure(config_dir=...)` appeared exactly once
    # in the tree and that once was `main.py`. A headless install is what a
    # gate box, a Steam Deck shortcut and every scripted install run, and when
    # one fails the terminal is often already closed.
    #
    # `stderr_level=WARNING` is what makes logging the stage lines below
    # affordable: they are on stdout already, a gate runs this as
    # `> log 2>&1`, and an INFO record through the stderr handler would print
    # every line of a 30-minute install twice. The file takes the run, the
    # terminal takes what went wrong.
    #
    # REJECTED — and this is an argument for NOT adding something, not for the
    # level above: a reporter of this harness's own, the CLI's answer to
    # `main.py`'s `_warn_about_the_log_file()` dialog. `main.py` needs one
    # because the frozen build is `console=False` and its stderr reaches
    # nobody, so a dialog is the only channel that survives packaging. This
    # harness already HAS the channel: `configure()` logs its own
    # `file_log_problem()` at WARNING, which is exactly the level named here,
    # so the sentence is on the terminal without another line of code. A
    # second reporter would be a duplicate of a message that is not even about
    # the install, on the one entry point whose output a gate reads line by
    # line. `test_a_harness_run_that_could_not_open_its_log_says_so_rather_
    # than_dying` is what holds the channel open.
    #
    # AFTER `parse_args`, so `--help` and a bad flag stay pure: they exit
    # without creating a config dir on a box that only asked for usage. Held
    # by `test_usage_and_a_bad_flag_leave_no_config_dir_behind`, which was
    # written 2026-09-05 after a review pointed out that moving this call up
    # three lines as a tidy would have broken it in silence.
    configure(config_dir=platform.config_dir(), stderr_level=logging.WARNING)
    logger.info(
        "install harness: game=%s server_dir=%s client_dir=%s",
        args.game,
        args.server_dir,
        args.client_dir,
    )
    try:
        entry = load_catalog().get(args.game)
    except KeyError:
        sys.stderr.write(f"unknown game {args.game!r}\n")
        return 2
    options = InstallOptions(server_dir=args.server_dir, client_dir=args.client_dir)
    # One event per run, as the Catalog tab makes one per install. It is the
    # only seam that stops a bridge worker whose consumer has gone: a `run()`
    # given no event leaves `_pump()`'s worker running on abandonment
    # (bug-checklist §21), and until 2026-09-04 this harness gave none — the
    # one install path every gate box runs was the case §21's own closing test
    # could not pass.
    cancel = threading.Event()
    try:
        engine = installer_for_app(entry, installers_root=args.installers_root)
        # `ask=` is not optional. Provisioning asks the docker-group consent and
        # the sudo password through it, and a run given no prompter answers
        # neither — which on a password-sudo box is the hang above.
        for line in engine.run(options, cancel=cancel, ask=_terminal_prompter):
            # STRIPPED OF T35's MARKERS, and this is the site the ticket names.
            # The gate transcripts in `.notes/gates/` are this stream and this
            # log file, and a control character in front of every relayed line
            # would put `\x1etool ` into every one of them — greppable by
            # nothing that greps them today. `lines.parse()` hands back the
            # display text, so every line a run used to write is byte for byte
            # what it writes now.
            #
            # **A progress READING is not written at all, and that is the whole
            # rule.** It is a header field on a screen this harness does not
            # have: one number that replaces the last one. Every relayed
            # reading already has its own raw line written beside it
            # (`lines.relayed()` yields both), so writing the reading too would
            # duplicate the 8005 tile lines of a CMaNGOS install with a
            # rephrased copy of each. The readings with no raw twin are git's
            # clone percentages, which this file never carried before T35
            # either — the clone was not streamed at all. So the transcript is
            # what it always was, plus git's own sentences.
            parsed = lines.parse(line)
            if parsed.kind == "progress":
                continue
            said = parsed.text
            sys.stdout.write(said + "\n")
            sys.stdout.flush()
            # Streamed first, recorded second: stdout is what a gate is reading
            # live, and the file is what is read afterwards. These lines are the
            # whole reason the file is worth opening — an install that failed
            # 20 minutes in is answerable only by which stages it got through.
            logger.info("%s", said)
    except InstallerError as exc:
        # Both, and not a duplicate to be tidied away: the record is what puts
        # the sentence in `yulon.log` (and carries a timestamp), and the plain
        # write is what reaches whatever `sys.stderr` is at this moment —
        # a handler binds its stream when it is constructed, at import, which
        # is before any redirection a caller or a test does.
        logger.error(f"install failed: {exc}")
        sys.stderr.write(f"install failed: {exc}\n")
        return 1
    except KeyboardInterrupt:
        # Ctrl+C, in either of the two places it can land. Measured on m910q
        # 2026-09-05, both of them through the real `main()`, the worker was
        # already stopped by the time this handler ran:
        #
        #   * inside `engine.run()`'s frame — where this thread spends an
        #     install, blocked in the bridge's `lines.get()`. The generator's
        #     own `except BaseException` calls `stop_abandoned_worker()` there.
        #   * inside the `sys.stdout.write` above. The `for` loop's iterator is
        #     the only reference to that generator, so unwinding to this
        #     handler drops it and CPython closes it: the SAME hook runs, as a
        #     `GeneratorExit`, and it runs BEFORE this line
        #     (`test_install_wiring.py`). An earlier version of this comment
        #     said that case "reaches no hook"; that was measured false.
        #
        # `cancel.set()` is kept anyway, because it costs nothing and does not
        # depend on refcounting: setting a set event is a no-op, and the only
        # reader is a worker that is already stopping. 130 is what a shell
        # reports for a SIGINT exit.
        cancel.set()
        sys.stderr.write("install stopped\n")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
