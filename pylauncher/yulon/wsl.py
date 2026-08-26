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


_DISTRO_NOT_FOUND_RETURNCODE = 0xFFFFFFFF
"""What `wsl.exe` exits with when it could not launch the command at all.

4294967295 - the unsigned DWORD Windows reports, which is the number Python
hands back. Measured 2026-08-26 with `wsl -d yulon-no-such-distro -- docker ps`
on the box this module was written for.

Deliberately NOT the same value written signed. `docker.CANCELLED_RETURNCODE`
is -1, so accepting -1 here would read a user pressing Cancel as a deleted
distro.
"""

_DISTRO_NOT_FOUND_CODE = "WSL_E_DISTRO_NOT_FOUND"
"""The symbolic half of wsl.exe's complaint, and the only half worth matching.

The sentence beside it - "There is no distribution with the supplied name." -
is translated, the same trap that made `wsl -l -v`'s STATE column unusable (see
`distro_states()`). The `Wsl/Service/WSL_E_DISTRO_NOT_FOUND` code is not.
"""


def missing_distro_problem(distro: str | None, returncode: int, output: str = "") -> str | None:
    """Why a docker command inside `distro` failed, if the distro itself is gone.

    A remembered distro can be deleted or renamed under a server that was
    adopted out of it, and what the user was shown for that was the raw failure:
    `docker ps exited 4294967295: ` - with nothing after the colon, because
    **wsl.exe writes this complaint to stdout, not stderr**, and stderr is what
    `docker._run()` quotes. Captured 2026-08-26:

        rc     : 4294967295
        stdout : 'T\\x00h\\x00e\\x00r\\x00e\\x00 \\x00i\\x00s\\x00 ...'
        stderr : ''

    The NULs are not damage: wsl.exe writes UTF-16LE, and `runner.run()` decodes
    as UTF-8, so every ASCII character arrives followed by `\\x00`. Matching
    survives that by removing them rather than by re-decoding, because the caller
    has already lost the bytes.

    **Why the translation lives here and not at the seam that raises.** There
    are three of those seams, all in `docker.py` - `_run()` for buffered calls,
    `follow_logs()` and `run_attached()` for the streamed ones - and a missing
    distro fails at all three. Putting the knowledge of wsl.exe's exit codes and
    UTF-16 output in each of them would spread this module's traps across the
    file that already carries the most review history. Every one of those seams
    already holds a `wsl_distro`, an exit code and the captured output, so this
    signature is what each needs to ask in one line, with no new parameter
    threaded anywhere.

    `distro` is `str | None` for the same reason: the seams hold exactly that,
    and a plain Windows docker failure has no distro to blame, so it is None
    here rather than an `if` at each call site.

    Two-tier on purpose. The error code settles it without spawning anything.
    Whether every wsl.exe build prints that code could not be captured here -
    only this box's was, and inventing the older one's output is exactly what
    this module's fixtures refuse to do - so a failure that does not carry it
    asks the listing instead. Asking the listing does not start any distro,
    unlike probing one (see `find_servers()`), and it only happens on a path
    that has already failed.

    **An EMPTY listing is not evidence of anything.** `_wsl_list()` answers `()`
    for four different things - no wsl.exe on PATH, `OSError`, a timeout, and a
    non-zero exit - and only one of them means "there are no distros". The
    condition that sends a failure down to tier 2 is a `0xFFFFFFFF` carrying no
    `WSL_E_DISTRO_NOT_FOUND`, which is WSL failing at the SERVICE level
    (LxssManager wedged, vmcompute down) - and that is exactly the state in
    which `wsl -l -q` also fails and returns `()`. Reading that silence as "the
    distro is gone" sent the user off to re-adopt a server that was never
    missing, in precisely the case tier 2 exists to judge (review, 2026-08-26).
    So the accusation needs a listing that ANSWERED and did not name the distro;
    anything else stays quiet and lets the raw failure through, which is
    unhelpful but true.
    """
    if distro is None or returncode != _DISTRO_NOT_FOUND_RETURNCODE:
        return None
    if _DISTRO_NOT_FOUND_CODE not in output.replace("\x00", ""):
        existing = distro_states()
        if not existing or any(state.name == distro for state in existing):
            # Either the distro is still there - merely stopped, or broken - or
            # the listing itself could not answer. Both mean this is not the
            # moment to tell someone their distro was deleted and send them off
            # to re-adopt a server that is exactly where they left it.
            return None
    return (
        f"The WSL distro {distro} no longer exists - it was deleted, or renamed. Everything on "
        f"this tab runs docker inside {distro}, so nothing here can start, stop or read the log "
        "until that distro is back under that name, or this server is adopted again with "
        '"Use existing…".'
    )


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
