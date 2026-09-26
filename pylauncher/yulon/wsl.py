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
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _wsl_listing(*args: str) -> tuple[str, ...] | None:
    """Names from one `wsl -l -q ...` listing, or None if it did not answer (T95).

    None for no wsl.exe, a raise (the timeout among them) and a non-zero exit:
    the caller that must not mistake "no answer" for "nothing" can tell them
    apart. Whether `--running` exits non-zero when nothing runs has not been
    measured, so a caller reading None as "unknown" may see it then too.
    """
    launcher = platform._which(platform.WSL_PROGRAM)
    if launcher is None:
        return None
    try:
        proc = subprocess.run(
            [launcher, "-l", "-q", *args],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
            creationflags=runner.creationflags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"could not list WSL distros: {exc}")
        return None
    if proc.returncode != 0:
        return None
    # UTF-16LE, like every other `wsl.exe` listing — see `platform.wsl_distros()`.
    return parse_distro_names(proc.stdout.decode("utf-16le", errors="ignore"))


def _wsl_list(*args: str) -> tuple[str, ...]:
    """Names from one `wsl -l -q ...` listing, or `()` if there is no WSL here."""
    return _wsl_listing(*args) or ()


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


def known_stopped(distro: str) -> bool:
    """True only if both listings ANSWERED: the full one names `distro`, `--running` does not.

    The fail-closed half of `is_running()` (T95 re-review), for a caller about
    to SKIP something because the distro is down. `distro_states()` reads a
    listing that did not answer as an empty one, so a `--running` call that
    timed out made a running distro look stopped, and a stop skipped on that
    left a server running. Any listing that did not answer is False here.
    """
    running = _wsl_listing("--running")
    if running is None:
        return False
    listed = _wsl_listing()
    if listed is None:
        return False
    return distro in listed and distro not in running


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
    are several of those seams, all in `docker.py`: `_run()` for buffered calls,
    `follow_logs()` and `run_attached()` for the streamed ones, and
    `volume_exists()`, which reads its own exit codes rather than going through
    `_run()` because the answer it wants IS a non-zero exit. A missing distro
    fails at every one. Putting the knowledge of wsl.exe's exit codes and
    UTF-16 output in each of them would spread this module's traps across the
    file that already carries the most review history. Every one of those seams
    already holds a `wsl_distro`, an exit code and the captured output, so this
    signature is what each needs to ask in one line, with no new parameter
    threaded anywhere.

    The list is *named*, not counted. It said "three" until `volume_exists()`
    became the fourth, and a bare number in another module's docstring has
    nothing that can notice; the names do, because
    `test_every_seam_that_asks_about_a_missing_distro_is_named_where_it_is_answered`
    reads the callers out of `docker.py`'s AST and requires each to appear here.

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


# -- holding a distro open while its server runs (T132) -----------------------------

HOLD_DIR = "/dev/shm/yulon-$(id -u)"
"""Where a hold keeps its lock and pid file, as the distro's shell spells it.

`/dev/shm` because it is tmpfs, emptied whenever the distro starts: a pid file that
survived a restart would name whatever process got that number next. A directory
of the user's own, mode 0700, because `/dev/shm` itself is world-writable and a
pid file another account could plant is a pid `release()` would be asked to end.
The scripts refuse a directory that is a symlink or is not owned by the caller.
"""

HOLD_ALREADY_HELD = 75
"""flock's exit code for "somebody holds this lock", chosen here with `-E`.

A second hold of a server that is already held must not stack a second
session, and must not read as a failure either: the distro IS held.
"""

HOLD_DIR_REFUSED = 76
"""The scripts' exit code for a hold directory that is not the caller's own."""

HOLD_SETTLE_SECONDS = 2.0
"""How long `hold()` waits before believing the session stayed up."""

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
"""Win32 process-creation flags, spelled as numbers so this module type-checks off Windows."""

_HOLD_TIMEOUT = 30

Popen = Callable[..., Any]
Run = Callable[[list[str]], object]


def _run_release(argv: list[str]) -> object:
    """The real run behind `release()`; a module attribute so the suite can refuse it."""
    return subprocess.run(
        argv, capture_output=True, timeout=_HOLD_TIMEOUT, creationflags=runner.creationflags()
    )


_spawn: Popen = subprocess.Popen
"""The real spawn behind `hold()`, looked up per call so the suite can refuse it
(`tests/conftest.py`): a unit test on a Windows box would otherwise hold a real
distro open for as long as that box runs."""


def _hold_name(key: str) -> str:
    """`key` as a file name the scripts can carry without quoting: nothing but [A-Za-z0-9_.-]."""
    return "yulon-hold-" + re.sub(r"[^A-Za-z0-9_.-]", "_", key)


_OWN_DIR = (
    f'd={HOLD_DIR}; mkdir -p -m 700 "$d" 2>/dev/null; '
    f'[ -d "$d" ] && [ ! -L "$d" ] && [ -O "$d" ] || exit {HOLD_DIR_REFUSED}; '
)
"""The prefix both scripts share: make the private directory, refuse one that is not ours."""


def hold_script(key: str) -> str:
    """The shell text that holds one session open: a `sleep` under an exclusive lock.

    `flock -n` makes a second hold for the same key exit at once with
    `HOLD_ALREADY_HELD` instead of stacking. The inner `sh` records its own pid
    AND that process's start time (field 22 of `/proc/<pid>/stat`, which `exec`
    keeps) and then becomes the `sleep`, so `release()` can tell the sleep it
    made from a later process that happened to get the same number.
    """
    name = _hold_name(key)
    return (
        _OWN_DIR + f'exec flock -n -E {HOLD_ALREADY_HELD} "$d/{name}.lock" '
        f'sh -c \'echo $$ $(cut -d" " -f22 /proc/$$/stat) > "$1/{name}.pid"; '
        'exec sleep infinity\' sh "$d"'
    )


def release_script(*keys: str) -> str:
    """The shell text that ends the holds for `keys`, each only if it is still the sleep it made.

    A pid is killed only when it is a `sleep` AND its start time is the one
    recorded with it: a recycled pid has another start time. The `sleep` holds
    the lock, so it is free again the moment the kill lands.
    """
    names = " ".join(_hold_name(key) for key in keys)
    return _OWN_DIR + (
        f"for n in {names}; do "
        'f="$d/$n.pid"; set -- $(cat "$f" 2>/dev/null); '
        'if [ -n "$1" ] && [ "$(cat /proc/$1/comm 2>/dev/null)" = sleep ] '
        '&& [ "$(cut -d" " -f22 /proc/$1/stat 2>/dev/null)" = "$2" ]; then kill "$1"; fi; '
        'rm -f "$f"; done'
    )


def _exec_argv(distro: str, script: str) -> list[str] | None:
    """`wsl -d <distro> --exec sh -c <script>`, or None without wsl.exe.

    `--exec` and not the `--` of `platform.wsl_prefix()`: after `--` wsl.exe
    hands the joined command line to the distro's login shell, which would
    expand the script's `$$` and `$(...)` for itself before `sh` ever saw them.
    """
    launcher = platform._which(platform.WSL_PROGRAM)
    if launcher is None:
        return None
    return [launcher, "-d", distro, "--exec", "sh", "-c", script]


def _detached_flags() -> int:
    """Creation flags for a child that must outlive this app: none off Windows."""
    if sys.platform != "win32":
        return 0
    return CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB


@dataclass
class Hold:
    """What `hold()` made: whether the distro is held, and the session if THIS call spawned it.

    `alive()` is how a caller learns a hold went away without asking the
    distro anything. Only the session this process spawned can be watched; a
    hold that was already there (a previous launch's, found by its lock)
    counts as alive, because nothing here can see it without a `wsl -d`.
    """

    held: bool
    proc: Any = None

    def alive(self) -> bool:
        if not self.held:
            return False
        return self.proc is None or self.proc.poll() is None


def hold(
    distro: str,
    key: str,
    *,
    popen: Popen | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Hold:
    """Keep `distro` running after this app exits, until `release(distro, key)`.

    **Why this exists (T132, measured on yulon-win11, WSL 2.7.12):** a distro
    stops 15-25 s after the last wsl.exe session attached to it exits. Neither
    systemd, nor a running dockerd, nor running containers keep it up, and
    Docker Desktop running beside it changes nothing; `vmIdleTimeout` is about
    the VM, not the distro. Start is one short `wsl -d ... compose up -d`, so a
    WSL-resident server lived only while the Server tab's five-second poll kept
    calling in, and was killed (not stopped) 15-25 s after the app closed.
    One held `wsl.exe -d <distro> -- sleep` kept the distro up indefinitely.

    So the hold is exactly that: one `wsl.exe` running `sleep infinity`,
    spawned detached so it outlives this process, found again by its pid file
    INSIDE the distro rather than by this process's memory, which is what lets
    the next launch's Stop end the hold the previous launch made.

    Idempotent through the lock, so a caller that is not sure may ask again:
    a second hold for the same key exits at once and counts as held.

    Never raises: a server that runs is not failed by its hold. `held` False
    means the distro may stop 15-25 s after the last Yu'lon call into it, which
    is how every WSL server behaved before this, and the log says why.
    """
    argv = _exec_argv(distro, hold_script(key))
    if argv is None:
        logger.debug(f"no {platform.WSL_PROGRAM}; nothing can hold {distro} open")
        return Hold(held=False)
    common: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    flags = _detached_flags()
    spawn = popen if popen is not None else _spawn
    try:
        try:
            proc = spawn(argv, creationflags=flags, **common)
        except OSError:
            if not flags & CREATE_BREAKAWAY_FROM_JOB:
                raise
            # A job that does not allow breakaway refuses the flag outright.
            # Without it the hold still outlives the app unless that job also
            # kills its members on close, which is the launcher's choice.
            # Not exercised live: no launcher Yu'lon ships in runs it in such a job.
            proc = spawn(argv, creationflags=flags & ~CREATE_BREAKAWAY_FROM_JOB, **common)
    except OSError as exc:
        logger.warning(f"could not hold {distro} open for {key}: {exc}")
        return Hold(held=False)
    sleep(HOLD_SETTLE_SECONDS)
    code = proc.poll()
    if code is None:
        logger.info(f"holding {distro} open while {key} runs")
        return Hold(held=True, proc=proc)
    if code == HOLD_ALREADY_HELD:
        logger.info(f"{distro} was already held open for {key}")
        return Hold(held=True)
    logger.warning(
        f"the session holding {distro} open for {key} exited at once ({code}); the distro "
        "may stop 15-25 s after Yu'lon's last call into it, taking the server with it"
    )
    return Hold(held=False)


def release(distro: str, *keys: str, run: Run | None = None) -> None:
    """End the holds `hold(distro, key)` made for `keys`, if the distro is up. Never raises.

    Several keys in one call because a Stop of ANOTHER install's server (the
    port-conflict path) knows the containers it stopped but not which of them
    that install keyed its hold by; releasing a key nobody held is a no-op.

    A stopped distro holds nothing, and asking it anything would start it
    (`pyplan/wsl-resident-servers.md` §2), so it is left alone -- but only when
    WSL SAID it is down (`known_stopped()`, T95's fail-closed reading): the
    caller has just stopped containers in this distro, and a listing that did
    not answer is no reason to leave the distro pinned open.
    """
    if not keys or known_stopped(distro):
        return
    argv = _exec_argv(distro, release_script(*keys))
    if argv is None:
        return
    try:
        (run if run is not None else _run_release)(argv)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"could not release the hold on {distro} for {', '.join(keys)}: {exc}")
        return
    logger.info(f"released the hold on {distro} for {', '.join(keys)}")
