"""`yulon --server start|stop|status <game> [<folder>]`: a WoW server's Start and Stop, headless.

For the Steam Deck's Gaming Mode tiles (`gaming_mode.WowGamingMode`): the "<game> Server" tile's
script asks Yu'lon to start the server and waits for the answer, and the "<game> Server Stop"
tile's asks it to stop. A script cannot do a WoW Start itself: the tab's Start reserves the
server against every other Yu'lon, refuses a missing image or database and an ended import,
keeps the realm's port right, renames bot settings, puts the zone file back, and starts the
three services staged so the one-shot database import never runs again. A bare
`docker compose up -d` would skip all of it, and could run that import. So this runs the
tab's own code: the controller `ControllerServices.for_entry()` builds for that install,
its `start()` / `stop_conflicting_and_start()` / `stop()`, and the ready wait the tab's
Restart uses (`native.ready_after_start()`, the install's own ready markers).

**One game at a time** (the Lab's rule on a Deck): a Start whose ports another server holds
stops that server first and says which, as the tab does when the player agrees to its
offer; the tile cannot ask, and the Lab's own launchers have always done this.

Answered before Qt starts an application, as `--provision` is (`main.py`); the tab's module
is imported, but no window, no tray and no single-instance claim are made, so a Yu'lon
already open on the same server is safe: both go through the same cross-process
reservation (T568). Exit codes are the tile script's control flow:

  0  done (start: the world reported ready; stop: stopped, or was not running)
  1  status only: the world is not up
  2  refused or failed, with the reason printed
  3  start only: the server started, but the world did not report ready
 64  the command line itself was wrong
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from yulon import platform
from yulon.log import get_logger

if TYPE_CHECKING:
    from yulon.controller import Controller
    from yulon.state import KnownInstall
    from yulon.ui.controller_view import ControllerServices

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_NOT_UP = 1
EXIT_REFUSED = 2
EXIT_NOT_READY = 3
EXIT_USAGE = 64

VERBS = ("start", "stop", "status")
USAGE = "usage: yulon --server start|stop|status <game id> [<server folder>]"
TICK_SECONDS = 5.0
"""How often a waiting Start prints a dot, as the Lab's launchers do."""

Say = Callable[[str], None]


def _say(text: str) -> None:
    print(text, flush=True)


def _install(
    game: str, folder: str | None, config_dir: Path | None
) -> tuple[KnownInstall | None, str]:
    """The known install of `game` (in `folder`, when given), or the reason there is none."""
    from yulon import state as state_module

    known = state_module.load_state(state_module.state_path(config_dir), repair=False)
    matches = [i for i in known.installs if i.game == game]
    if folder is not None:
        wanted = Path(folder)
        matches = [i for i in matches if i.server_dir == wanted]
    if not matches:
        where = f" in {folder}" if folder is not None else ""
        return None, f"Yu'lon has no {game} install{where}. Nothing was done."
    if len(matches) > 1:
        folders = ", ".join(str(i.server_dir) for i in matches)
        return None, f"Yu'lon has more than one {game} install ({folders}); name the folder."
    return matches[0], ""


def run(
    argv: Sequence[str],
    *,
    say: Say = _say,
    config_dir: Path | None = None,
    services_for: Callable[..., ControllerServices] | None = None,
    tick: float = TICK_SECONDS,
) -> int:
    """Do `argv` (`<verb> <game> [<folder>]`); the exit code above. Every seam injectable."""
    if len(argv) not in (2, 3) or argv[0] not in VERBS:
        say(USAGE)
        return EXIT_USAGE
    verb, game = argv[0], argv[1]
    folder = argv[2] if len(argv) == 3 else None

    from yulon.catalog.catalog import load_catalog

    try:
        entry = load_catalog().get(game)
    except (KeyError, ValueError):
        say(f"{game} is not a game this Yu'lon can run. Nothing was done.")
        return EXIT_USAGE
    install, problem = _install(game, folder, config_dir)
    if install is None:
        say(problem)
        return EXIT_REFUSED
    if services_for is None:
        from yulon.ui.controller_view import ControllerServices

        services_for = ControllerServices.for_entry
    services = services_for(
        entry,
        install.server_dir,
        client_dir=install.client_dir,
        wsl_distro=install.wsl_distro,
        play_client_dir=install.play_client_dir,
    )
    controller = services.controller
    name = entry.name
    try:
        if verb == "status":
            status = controller.status()
            say("running" if status.all_running else "stopped")
            return EXIT_OK if status.world else EXIT_NOT_UP
        if verb == "stop":
            stopped = controller.stop(by_choice=True)
            say(f"{name} stopped." if stopped is not False else f"{name} was not running.")
            return EXIT_OK
        return _start(controller, services, name, say=say, tick=tick)
    except Exception as exc:  # noqa: BLE001 - every refusal is printed, never a traceback
        logger.warning(f"--server {verb} {game}: {type(exc).__name__}: {exc}")
        say(_words(exc, name, verb))
        return EXIT_REFUSED


def _start(
    controller: Controller, services: ControllerServices, name: str, *, say: Say, tick: float
) -> int:
    from yulon.controller import PortConflictError

    try:
        controller.start()
    except PortConflictError as exc:
        holder = exc.owner_summary()
        say(
            f"{holder[:1].upper()}{holder[1:]} holds the ports {name} needs. Stopping it "
            "first: one game at a time."
        )
        stopped = controller.stop_conflicting_and_start()
        if stopped:
            say("Stopped " + ", ".join(stopped) + ".")
    for note in (
        getattr(controller, "zone_problem", None),
        getattr(controller, "world_data_problem", None),
        getattr(controller, "bot_settings_renamed", None),
    ):
        if isinstance(note, str) and note:
            say(note)
    wait = services.ready_after_start
    if wait is None:
        say(f"{name} started.")
        return EXIT_OK
    say(f"{name} started; waiting for its world to say it is ready.")
    done = threading.Event()

    def dots() -> None:
        while not done.wait(tick):
            sys.stdout.write(".")
            sys.stdout.flush()

    ticker = threading.Thread(target=dots, daemon=True)
    ticker.start()
    try:
        answer = wait()
    finally:
        done.set()
        ticker.join(1.0)
        sys.stdout.write("\n")
        sys.stdout.flush()
    if answer.ready:
        say(f"{name} is ready.")
        return EXIT_OK
    say(f"{name} started, but its world did not report ready ({answer.verdict}).")
    if answer.words:
        say(answer.words.strip())
    return EXIT_NOT_READY


def _words(exc: BaseException, name: str, verb: str) -> str:
    """The sentence a refusal prints: Yu'lon's own when it wrote one, else a plain one."""
    text = str(exc).strip()
    if text:
        return text
    return f"{name} could not {verb}: {type(exc).__name__}."


def main(argv: Sequence[str]) -> int:
    """`main.py`'s entry for `--server`: logging is already configured."""
    logger.info(f"Yu'lon --server {' '.join(argv)} (headless, {platform.detect()})")
    return run(argv)
