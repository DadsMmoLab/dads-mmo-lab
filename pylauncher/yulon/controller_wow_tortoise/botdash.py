"""The Bots tab's "Bot dashboard" switch for a Tortoise install: the Docker half (T127).

`yulon.catalog.bot_dashboard` owns what the switch writes into the install's two
files; this module builds the image, starts and removes the container, and
restarts the world, and everything it does runs off the GUI thread as a line
source for the tab's log panel.

**On**, in this order, and each step is where it is for a reason:

1. the refusals, before anything is written: no dashboard in this module's
   checkout, a compose file this app did not write, a mixed SELinux label;
2. the image, built from `tools/observability` in the module checkout the
   install already cloned. First because it is the slow step (a Go build, a
   few minutes the first time) and the one most likely to fail -- a failure
   here has changed nothing;
3. `.env` gets this install's session secret if it has none;
4. the conf is backed up once (`.before-dashboard`, `copy2`, so the mode goes
   with it) and the three keys are written, keeping the file's mode;
5. the service block goes into `docker-compose.yml`;
6. the container is started, BEFORE the world restarts: the module resolves the
   service name once, when it loads;
7. the world is restarted if it was running, so it reads the conf.

A failure in 4-6 puts the conf and the compose file back and removes the
container, so the switch never ends half on.

**Off** removes the container (while the block still names it), takes the block
out, puts the three keys back to what the backup says and removes the image. The
world keeps sending to an address nobody listens on until it restarts, which is
harmless; the tab offers the restart.
"""

from __future__ import annotations

import queue
import shutil
import threading
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from yulon import docker
from yulon.catalog import bot_dashboard as files
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.native import LatestRoute, stop_abandoned_worker
from yulon.controller import Controller
from yulon.controller_wow_tortoise import botpool
from yulon.log import get_logger
from yulon.ui import lines

logger = get_logger(__name__)

DOCKERFILE_DIR = "tools/observability"
"""Where the bots module keeps the daemon's Dockerfile (upstream's layout since it shipped)."""

Press = Callable[[threading.Event | None], Iterator[str]]

START_HOOK_TIMEOUT_S = 120.0
"""How long the server's Start waits for the dashboard's `compose up` before going on without it.

The image is already built, so a healthy start takes a second or two; the bound
is there so a daemon that hangs can never hold the server's own Start."""


class SwitchError(RuntimeError):
    """A refusal or a failure, in the words the log panel shows."""


@dataclass
class Dashboard:
    """One install's dashboard switch. Every method does IO: call it off the GUI thread."""

    entry: CatalogEntry
    server_dir: Path
    controller: Controller
    wsl_distro: str | None = None

    url: str = files.URL

    # -- readings ---------------------------------------------------------

    def state(self) -> files.State:
        return files.state(self.server_dir)

    def world_running(self) -> bool | None:
        return docker.world_running(self.entry.containers.world, wsl_distro=self.wsl_distro)

    # -- on ---------------------------------------------------------------

    def switch_on(self, *, lan: bool, cancel: threading.Event | None = None) -> Iterator[str]:
        """Build, write, start, restart. Yields what it is doing; raises `SwitchError`."""
        conf = self._conf_path()
        context = self._context()
        base = files.base_path(self.server_dir)
        text = self._read_ours(base)
        if files.block_in(text) is not None:
            raise SwitchError("The bot dashboard is already switched on for this server.")
        try:
            label = files.bind_label(text)
        except files.DashboardError as exc:
            raise SwitchError(str(exc)) from exc
        service = files.service(self.entry)
        image = files.image_ref(self.entry, self.server_dir)

        yield (
            "Building the bot dashboard from the bots module's own source. The first build "
            "downloads Go and takes a few minutes…"
        )
        run = yield from _streamed(
            lambda sink: docker.build_image(
                context, image, wsl_distro=self.wsl_distro, sink=sink, cancel=cancel
            ),
            cancel=cancel,
        )
        if run.returncode == docker.CANCELLED_RETURNCODE:
            raise SwitchError("Stopped before anything was changed. The dashboard is still off.")
        if run.returncode != 0:
            raise SwitchError(
                f"The dashboard could not be built (exit {run.returncode}), so nothing was "
                f"changed. Its last words were: {docker.last_words(run.tail, from_build=True)}"
            )
        if cancel is not None and cancel.is_set():
            raise SwitchError("Stopped before anything was changed. The dashboard is still off.")

        self._ensure_secret()
        world_was_up = self.world_running()
        before_conf = files.read_exact(conf)
        try:
            backup = conf.with_name(conf.name + files.CONF_BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy2(conf, backup)
            after_conf = files.patch_text(before_conf, files.conf_keys(self.entry))
            if after_conf != before_conf:
                files.write_keeping_mode(conf, after_conf)
            yield f"Told the bots module to send to the dashboard ({conf.name})."
            dbc = (self.server_dir / files.DBC_DIR).is_dir()
            new_block = files.block(self.entry, self.server_dir, lan=lan, label=label, dbc=dbc)
            files.write_keeping_mode(base, files.add(text, new_block))
            yield f"Added the dashboard to {base.name}."
            docker.compose_up_service(self.server_dir, service, wsl_distro=self.wsl_distro)
        except (OSError, files.DashboardError, docker.DockerCommandError) as exc:
            yield from self._undo(text, before_conf)
            raise SwitchError(
                f"The dashboard could not be switched on ({exc}). Everything it changed was put "
                "back."
            ) from exc
        where = "this PC only" if not lan else "every device on your network"
        yield f"The dashboard is running at {self.url}, open to {where}."
        if not dbc:
            yield (
                f"This install has no {files.DBC_DIR} folder, so the Armory groups class spells "
                "by name rather than from the game's own files."
            )
        if world_was_up:
            yield "Restarting the world so the bots module starts sending…"
            yield from self._restart()
        else:
            yield "The server is stopped. The bots start sending when you start it."

    def _undo(self, compose_text: str, conf_text: str) -> Iterator[str]:
        """Best effort, every step tried: the container, the compose file, the conf."""
        service = files.service(self.entry)
        base = files.base_path(self.server_dir)
        conf = self._conf_path()
        try:
            if files.block_in(files.read_exact(base)) is not None:
                docker.compose_remove_service(self.server_dir, service, wsl_distro=self.wsl_distro)
        except (OSError, docker.DockerCommandError) as exc:
            logger.warning(f"could not remove the dashboard container: {exc}")
        for path, text in ((base, compose_text), (conf, conf_text)):
            try:
                if files.read_exact(path) != text:
                    files.write_keeping_mode(path, text)
            except OSError as exc:
                logger.warning(f"could not put {path} back: {exc}")
                yield f"Could not put {path.name} back ({exc})."
        backup = conf.with_name(conf.name + files.CONF_BACKUP_SUFFIX)
        backup.unlink(missing_ok=True)

    # -- off --------------------------------------------------------------

    def switch_off(self, cancel: threading.Event | None = None) -> Iterator[str]:
        """Conf first, then the container, then the block. The restart is the tab's question.

        **The block is the only record that the switch is on, so it goes LAST.**
        The first version removed the container and the block and then put the
        conf back; a conf that could not be written then left a tab reading Off
        over a world still sending telemetry, and no press that would retry
        (Codex review, 2026-09-25). Now each step either finishes or leaves the
        switch On with the block in place, so pressing Off again picks up where
        this one stopped:

        1. the three keys go back (a failure changes nothing else);
        2. the container is removed (a failure writes the keys on again, so the
           files still agree with a running dashboard);
        3. the block comes out of `docker-compose.yml`;
        4. only then the conf's backup and the image go.
        """
        del cancel  # each step is short and every one of them should finish
        conf = self._conf_path()
        base = files.base_path(self.server_dir)
        text = self._read_ours(base)
        service = files.service(self.entry)
        on = files.block_in(text) is not None
        try:
            yield from self._put_the_conf_back(conf)
        except OSError as exc:
            raise SwitchError(
                f"{conf.name} could not be put back ({exc}), so the dashboard was left ON and "
                "nothing else was changed. Fix that, then press the switch again."
            ) from exc
        if on:
            try:
                docker.compose_remove_service(self.server_dir, service, wsl_distro=self.wsl_distro)
            except docker.DockerCommandError as exc:
                self._keys_on_again(conf)
                raise SwitchError(
                    f"The dashboard's container could not be removed ({exc}), so it was left ON. "
                    "Press the switch again to retry."
                ) from exc
            yield "Stopped and removed the dashboard."
            try:
                files.write_keeping_mode(base, files.remove(text))
            except OSError as exc:
                raise SwitchError(
                    f"{base.name} could not be written ({exc}), so the dashboard still reads ON "
                    "there. Its container is gone and the bots module no longer sends to it. "
                    "Press the switch again to finish."
                ) from exc
            yield f"Took the dashboard out of {base.name}."
        conf.with_name(conf.name + files.CONF_BACKUP_SUFFIX).unlink(missing_ok=True)
        said = docker.remove_image(
            files.image_ref(self.entry, self.server_dir), wsl_distro=self.wsl_distro
        )
        if said:
            yield f"The dashboard's image was left on this PC ({said})."
        yield "The bot dashboard is off."

    def _keys_on_again(self, conf: Path) -> None:
        try:
            now = files.read_exact(conf)
            after = files.patch_text(now, files.conf_keys(self.entry))
            if after != now:
                files.write_keeping_mode(conf, after)
        except OSError as exc:  # the Start hook re-asserts them; this is the best effort
            logger.warning(f"could not write the dashboard's keys back into {conf}: {exc}")

    def _put_the_conf_back(self, conf: Path) -> Iterator[str]:
        """Each key back to what the backup says; one the backup lacked is removed.

        An inverse patch, not a restore, for `channel_setup._restore_the_conf()`'s
        reason: the backup can be old, and restoring a whole user-editable file
        to undo three keys would throw away every other edit made since. With no
        backup the switch is still turned off, and the host and port are left.
        The backup is NOT removed here: until the block is gone it is what a
        second press restores from. Raises `OSError` having written nothing.
        """
        backup = conf.with_name(conf.name + files.CONF_BACKUP_SUFFIX)
        now = files.read_exact(conf)
        keys = tuple(files.conf_keys(self.entry))
        if backup.is_file():
            was = files.conf_values(files.read_exact(backup), keys)
            put_back = {k: v for k, v in was.items() if v is not None}
            drop = [k for k, v in was.items() if v is None]
            after = files.patch_text(now, put_back) if put_back else now
            if drop:
                after = files.without_keys(after, drop)
        else:
            after = files.patch_text(now, {files.SWITCH_KEY: "0"})
        if after != now:
            files.write_keeping_mode(conf, after)
        yield f"Put the bots module's telemetry settings back ({conf.name})."

    # -- the restart ------------------------------------------------------

    def restart_world(self, cancel: threading.Event | None = None) -> Iterator[str]:
        """The Off question's Yes: stop, then start, through the Server tab's own controller."""
        del cancel
        yield "Restarting the world…"
        yield from self._restart()

    def _restart(self) -> Iterator[str]:
        try:
            botpool.restart_world(self.controller)
        except Exception as exc:  # noqa: BLE001 - the switch is done; this is one press left
            logger.warning(f"the restart after the dashboard switch failed: {exc}")
            yield (
                f"The restart failed ({exc}). Restart the server from the Server tab so the "
                "bots module reads its settings."
            )
            return
        yield "Restarted. The world takes a few minutes to come up; the bots show on the map then."

    # -- helpers ----------------------------------------------------------

    def _conf_path(self) -> Path:
        conf = files.conf_path(self.entry, self.server_dir)
        if conf is None:
            raise SwitchError(f"{self.entry.name} has no bot dashboard.")
        if not conf.is_file():
            raise SwitchError(
                f"{conf} is not there, so the bots module cannot be told about the dashboard. "
                "Nothing was changed."
            )
        return conf

    def _context(self) -> Path:
        module = botpool.module_dir(self.entry, self.server_dir)
        context = None if module is None else module / DOCKERFILE_DIR
        if context is None or not (context / "Dockerfile").is_file():
            raise SwitchError(
                "The bots module in this install has no dashboard to build "
                f"({DOCKERFILE_DIR}/Dockerfile is missing). Update the server to get a module "
                "that has one. Nothing was changed."
            )
        return context

    def _read_ours(self, base: Path) -> str:
        try:
            text = files.read_exact(base)
        except OSError as exc:
            raise SwitchError(
                f"{base.name} could not be read ({exc}). Nothing was changed."
            ) from exc
        if not composegen.is_marker_line(text):
            raise SwitchError(
                f"{base.name} was not written by Yu'lon, so the dashboard was not added to it. "
                "Nothing was changed."
            )
        return text

    def _ensure_secret(self) -> None:
        env = self.server_dir / composegen.DOTENV_FILE
        try:
            existing = env.read_text(encoding="utf-8") if env.is_file() else ""
        except OSError as exc:
            raise SwitchError(
                f"{env.name} could not be read ({exc}). Nothing was changed."
            ) from exc
        if any(
            line.strip().startswith(f"{files.SECRET_VAR}=")
            and line.strip() != f"{files.SECRET_VAR}="
            for line in existing.splitlines()
        ):
            return
        composegen.write_dotenv(self.server_dir, {files.SECRET_VAR: files.new_secret()})


def _streamed(
    call: Callable[[docker.OutputSink], docker.AttachedRun],
    *,
    cancel: threading.Event | None,
) -> Generator[str, None, docker.AttachedRun]:
    """A push-only docker run as lines, live: `native.InstallerEngine._pump()`'s bridge."""
    queued: queue.Queue[str | None] = queue.Queue()
    outcome: list[docker.AttachedRun] = []
    failure: list[BaseException] = []

    def put(line: str) -> None:
        for record in lines.relayed(line, stage="dashboard build"):
            queued.put(record)

    def work() -> None:
        try:
            outcome.append(call(put))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            failure.append(exc)
        finally:
            queued.put(None)

    worker = threading.Thread(target=work, daemon=True, name="yulon-dashboard-build")
    worker.start()
    try:
        while True:
            item = queued.get()
            if item is None:
                break
            yield item
    except BaseException:
        stop_abandoned_worker(worker, cancel, what="the dashboard build")
        raise
    worker.join()
    if failure:
        raise SwitchError(f"the build could not be run: {failure[0]}") from failure[0]
    return outcome[0]


# -- the Start hook and the update route -----------------------------------------


def start_if_on(entry: CatalogEntry, server_dir: Path, *, wsl_distro: str | None = None) -> None:
    """Before the world starts: the three conf keys re-asserted, and the dashboard up. Never raises.

    The Tortoise controller calls this from `start()`. The app's own Stop is
    `compose stop`, which stops the dashboard with everything else, and its
    Start names three services with `--no-deps` -- so without this a dashboard
    switched on stays down after the next Stop and Start. And the conf is
    re-asserted because a writer this app may run between two starts (the
    Tuning tab's Reset to default, T94) writes the catalog's table back: the
    switch is the compose block, and the conf follows it.
    """
    if files.conf_file(entry) is None or not files.is_on(server_dir):
        return
    conf = files.conf_path(entry, server_dir)
    try:
        if conf is not None and conf.is_file():
            now = files.read_exact(conf)
            after = files.patch_text(now, files.conf_keys(entry))
            if after != now:
                files.write_keeping_mode(conf, after)
                logger.info(f"put the bot dashboard's keys back into {conf}")
        docker.compose_up_service(
            server_dir, files.service(entry), timeout=START_HOOK_TIMEOUT_S, wsl_distro=wsl_distro
        )
    except Exception as exc:  # noqa: BLE001 - the dashboard must never stop a server starting
        logger.warning(f"the bot dashboard could not be started with the server: {exc}")


def after_update(
    update: Press,
    cancel: threading.Event | None,
    *,
    dashboard: Dashboard,
) -> Iterator[str]:
    """Run the update, then rebuild the dashboard from the module it moved. Never fails the update.

    The update keeps the dashboard's container running on its old image (the
    rebuild stops and recreates the servers, not this service), so the switch
    survives it by itself. What would go stale is the image: it was built from
    the module checkout the update just moved. It is rebuilt and the container
    recreated; if that gave the container a new address, the world -- which
    resolved the old one when it loaded -- is restarted.
    """
    yield from update(cancel)
    if not files.is_on(dashboard.server_dir):
        return
    entry, server_dir, distro = dashboard.entry, dashboard.server_dir, dashboard.wsl_distro
    service = files.service(entry)
    yield "Rebuilding the bot dashboard from the updated bots module…"
    try:
        context = dashboard._context()
        run = yield from _streamed(
            lambda sink: docker.build_image(
                context,
                files.image_ref(entry, server_dir),
                wsl_distro=distro,
                sink=sink,
                cancel=cancel,
            ),
            cancel=cancel,
        )
        if run.returncode != 0:
            yield (
                "The dashboard could not be rebuilt, so it keeps running the version it had. "
                f"Its last words were: {docker.last_words(run.tail, from_build=True)}"
            )
            return
        before = docker.container_ip(service, wsl_distro=distro)
        docker.compose_up_service(server_dir, service, force_recreate=True, wsl_distro=distro)
        after = docker.container_ip(service, wsl_distro=distro)
    except (SwitchError, docker.DockerCommandError, OSError) as exc:
        yield f"The dashboard could not be rebuilt ({exc}); switch it off and on to try again."
        return
    yield "The dashboard is running the updated version."
    if before is not None and before == after:
        return
    if dashboard.world_running() is not True:
        return
    yield "The dashboard came back on a new address, so the world is restarted to find it…"
    yield from dashboard._restart()


def wrap_route(route: LatestRoute | None, dashboard: Dashboard | None) -> LatestRoute | None:
    """The update route with `after_update()` around both presses; unchanged when it cannot be."""
    if route is None or dashboard is None:
        return route

    def wrapped(press: Press) -> Press:
        def run(cancel: threading.Event | None) -> Iterator[str]:
            return after_update(press, cancel, dashboard=dashboard)

        return run

    return replace(route, press=wrapped(route.press), to_pin=wrapped(route.to_pin))


def for_entry(
    entry: CatalogEntry,
    server_dir: Path,
    controller: Controller,
    *,
    wsl_distro: str | None = None,
) -> Dashboard | None:
    """The switch for this install, or None for a game whose conf table has no telemetry key."""
    if files.conf_file(entry) is None:
        return None
    return Dashboard(entry, server_dir, controller, wsl_distro=wsl_distro)
