"""Finding WoW servers that live inside a WSL2 distro.

A server built by the DML Launcher runs on Docker CE *inside* a distro, not on
Docker Desktop on the Windows side. Yu'lon is replacing that launcher, so those
servers have to be adoptable rather than merely refused — see
`pyplan/wsl-resident-servers.md` for the design and the spike behind it.

**Discovery asks Docker, not the filesystem.** `docker compose ls` already knows
every project and the exact path of its config files, so nothing here scans for
`~/games/*` or parses another product's folder conventions. That is what keeps
Yu'lon uncoupled from the DML Launcher's layout: it can reorganise freely and
this module never notices.

Windows-only in practice. Every entry point answers "nothing found" off Windows
rather than raising, because a caller asking what is available does not want an
exception for the ordinary case of there being nothing.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, runner
from yulon.log import get_logger

logger = get_logger(__name__)

_PROBE_TIMEOUT = 60


@dataclass(frozen=True)
class Distro:
    """One WSL distro, and whether it is running right now."""

    name: str
    running: bool


@dataclass(frozen=True)
class FoundServer:
    """A compose project inside a distro, in terms the rest of the app can use."""

    distro: str
    project: str
    running: bool
    server_dir: Path
    """The project's folder in its Windows UNC form.

    Docker answers in the distro's own spelling (`/home/dml/...`), but every
    Windows-side consumer — the compose-file check, the folder rule, the tab's
    label — needs the UNC form, so the conversion happens once here rather than
    at each of them.
    """


def parse_distro_names(text: str) -> tuple[str, ...]:
    """Distro names from `wsl -l -q` output that has already been decoded.

    `-q` prints names and nothing else - no header, no `*` marking the default,
    and crucially no STATE column, which is the one part `wsl.exe` translates.
    """
    return tuple(name for name in (line.strip() for line in text.splitlines()) if name)


def _wsl_list(*args: str) -> tuple[str, ...]:
    """Names from one `wsl -l -q ...` listing, or `()` if there is no WSL here."""
    launcher = platform._which(platform.WSL_PROGRAM)
    if launcher is None:
        return ()
    try:
        proc = subprocess.run(
            [launcher, "-l", "-q", *args],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
            creationflags=runner.creationflags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"could not list WSL distros: {exc}")
        return ()
    if proc.returncode != 0:
        return ()
    # UTF-16LE, like every other `wsl.exe` listing — see `platform.wsl_distros()`.
    return parse_distro_names(proc.stdout.decode("utf-16le", errors="ignore"))


def distro_states() -> tuple[Distro, ...]:
    """This machine's distros and whether each is running, or `()` if there is no WSL.

    Two `-q` listings rather than one `wsl -l -v`, because **`-v`'s STATE column
    is translated**. On German Windows a running distro reads "Wird ausgeführt",
    so a `state == "running"` test is False for every distro, every one looks
    stopped, and discovery finds nothing at all — on a machine where everything
    is working. `-q --running` answers with names only, which no locale changes.
    """
    running = set(_wsl_list("--running"))
    return tuple(Distro(name=name, running=name in running) for name in _wsl_list())


def is_running(distro: str) -> bool:
    """True if `distro` is up right now, WITHOUT starting it.

    Reads the listing rather than running anything inside the distro, because
    running anything is what starts one. Callers that poll - the Server tab
    refreshes every five seconds - must ask this first, or opening the app boots
    every distro it has ever adopted a server from.
    """
    return any(d.name == distro and d.running for d in distro_states())


def parse_compose_ls(distro: str, stdout: str) -> tuple[FoundServer, ...]:
    """Turn `docker compose ls --all --format json` into servers we could adopt.

    Bad input is "no servers", never an exception: an older compose without
    `--format json`, or an error printed to stdout, must not take down the
    dialog the user opened to look around.
    """
    try:
        projects = json.loads(stdout or "[]")
    except (ValueError, TypeError):
        logger.debug(f"{distro}: `docker compose ls` did not answer with JSON")
        return ()
    if not isinstance(projects, list):
        return ()

    found: list[FoundServer] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        name = str(project.get("Name") or "")
        configs = str(project.get("ConfigFiles") or "")
        first = configs.split(",")[0].strip()
        if not name or not first:
            # No config path means nothing to adopt and no folder to show.
            continue
        if first.startswith("/mnt/"):
            # A Windows folder mounted INTO the distro, not a server living in
            # it - which is what Docker Desktop's own integration distros
            # surface: the user's ordinary Windows projects. Adopting one would
            # hand back \\wsl.localhost\<distro>\mnt\c\... , a local folder
            # reached the long way round and then managed through the wrong
            # daemon. Those are attachable with "Use existing…" as themselves.
            logger.debug(f"{distro}: {name} lives on a Windows mount ({first}); not a WSL server")
            continue
        server_dir = platform.wsl_unc_path(distro, str(Path(first).parent).replace("\\", "/"))
        if server_dir is None:
            continue
        found.append(
            FoundServer(
                distro=distro,
                project=name,
                # `running(1)`, `exited(3)`, `paused(2)` - only the first says up.
                running=str(project.get("Status") or "").startswith("running"),
                server_dir=server_dir,
            )
        )
    return tuple(found)


def _compose_ls(distro: str) -> str:
    """Raw `docker compose ls` output from inside `distro`."""
    prefix = platform.docker_prefix(distro)
    if prefix is None:
        return ""
    proc = subprocess.run(
        [*prefix, "compose", "ls", "--all", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT,
        creationflags=runner.creationflags(),
    )
    return proc.stdout if proc.returncode == 0 else ""


def find_servers(include: tuple[str, ...] = ()) -> tuple[FoundServer, ...]:
    """Every compose project in the running distros, plus any named in `include`.

    **Stopped distros are not probed.** Running anything in a distro STARTS it —
    measured: `wsl -d docker-desktop -- true` flipped that distro from Stopped
    to Running — so scanning everything would boot everything, slowly, as a side
    effect of opening a dialog. A user who knows their server is in a stopped
    distro names it in `include`, and the caller is expected to have told them
    that checking will start it.

    One distro failing does not hide the others: a broken or unreachable distro
    is logged and skipped.
    """
    found: list[FoundServer] = []
    for distro in distro_states():
        if not distro.running and distro.name not in include:
            continue
        try:
            stdout = _compose_ls(distro.name)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug(f"{distro.name}: could not ask docker what it has: {exc}")
            continue
        found.extend(parse_compose_ls(distro.name, stdout))
    return tuple(found)
