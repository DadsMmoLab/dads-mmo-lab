"""What the Server tab says when Yu'lon cannot ask Docker, per machine and per failure (T194).

One title for every case, shared with the launcher window's banner and meant by
the realm badge's STATUS UNKNOWN, so the three never word it three ways. The
body is what to do on THIS machine, and `action` names the press the banner
carries beside Try again: open Docker Desktop (Windows, macOS), or the SteamOS
reinstall (a Steam Deck an update took Docker from). A Linux machine and a
server inside a WSL distro get words and Try again only: neither has a Docker
Desktop to open.

No Qt here, so every sentence is tested without a widget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from yulon import docker, platform

Host = Literal["windows", "macos", "linux", "deck"]
Problem = Literal[
    "missing", "not-running", "not-answering", "permission", "removed", "wsl", "unknown"
]
Action = Literal["open-desktop", "reinstall-deck"]

UNKNOWN_TITLE = "Yu'lon can't ask Docker about this server right now"
"""The banner's title, the launcher window's banner, and what STATUS UNKNOWN stands for."""

_DESKTOP_NOT_RUNNING = (
    "Docker Desktop isn't running. Open it and wait until it says Engine running; "
    "Yu'lon checks again every few seconds."
)
_ENGINE_NOT_RUNNING = (
    "Docker is installed but not running. Restart the computer, or run "
    '"sudo systemctl start docker" in a terminal.'
)
_DECK_NOT_RUNNING = (
    "Docker is installed but not running. Restart the Deck, or run "
    '"sudo systemctl start docker" in a terminal in Desktop Mode.'
)
_ENGINE_NOT_ANSWERING = (
    "Docker isn't answering. Restart it with "
    '"sudo systemctl restart docker" in a terminal (or restart the computer); '
    "Yu'lon checks again every few seconds."
)
_DECK_NOT_ANSWERING = (
    "Docker isn't answering. Restart the Deck, or run "
    '"sudo systemctl restart docker" in a terminal in Desktop Mode.'
)
_ENGINE_PERMISSION = (
    "Docker is running, but your account isn't allowed to use it yet. Log out and back in "
    "(or restart the computer) so your account joins the docker group, then press Try again."
)
_DECK_PERMISSION = (
    "Docker is running, but your account isn't allowed to use it yet. Restart the Deck so "
    "your account joins the docker group, then press Try again."
)
_WINDOWS_PERMISSION = (
    "Docker Desktop didn't let Yu'lon in. Sign out of Windows and back in (your account "
    "must be in the docker-users group), then press Try again."
)
_MACOS_PERMISSION = (
    "Docker Desktop didn't let Yu'lon in. Quit Docker Desktop, open it again, wait until it "
    "says Engine running, then press Try again."
)
_WSL_NOT_RUNNING = (
    "This server runs inside the WSL distro {distro}, and the Docker in that distro isn't "
    'answering. Open a terminal in {distro} and run "sudo systemctl start docker" (or restart '
    "the computer), then press Try again."
)
_UNKNOWN = (
    "Docker answered with an error Yu'lon doesn't recognise. The Logs tab has what it "
    "said; press Try again once it's sorted."
)
_WSL_UNNAMED = (
    "This server runs inside a WSL distro, and the Docker in that distro isn't answering. Open "
    'a terminal in it and run "sudo systemctl start docker" (or restart the computer), then '
    "press Try again."
)


@dataclass(frozen=True)
class Advice:
    """One banner: its title, what to do, and which press it carries beside Try again."""

    title: str
    body: str
    action: Action | None


def this_host() -> Host:
    """The machine as the banner tells it apart: a Steam Deck is not just Linux."""
    if platform.is_steamos():
        return "deck"
    return platform.detect()


_UNREACHABLE_WORDINGS = (
    "error during connect",
    "cannot connect to the docker daemon",
    "failed to connect to the docker api",
    "permission denied while trying to connect",
    "//./pipe/",
)
"""How Docker's CLI says it reached no daemon: Windows' pipe, a stopped Linux
daemon (old and new wording), a refused socket. See `docker.volume_exists()`."""


_DESKTOP_ENGINE_PIPES = (
    "dockerdesktoplinuxengine",
    "docker_engine",
    ".docker/run/docker.sock",
    ".docker%2frun%2fdocker.sock",
    "docker.raw.sock",
)
"""Docker Desktop's engine pipes (Windows) and sockets (macOS: `~/.docker/run/docker.sock`,
`…/com.docker.docker/Data/docker.raw.sock`), plain and as the CLI URL-encodes them in its API
route, lower-cased. A Linux daemon's `/var/run/docker.sock` is none of them."""


_DESKTOP_PROXY_ANSWERS = ("internal server error", "502 bad gateway")
"""What Docker Desktop's proxy answers on its engine pipe with no engine behind it, lower-cased:
500 while the engine starts or after it stopped, and (Docker 29) 502 the moment its VM is killed."""


def unreachable(exc: object) -> bool:
    """Whether `exc` is Docker not answering at all, rather than Docker refusing something.

    A missing CLI is not: it has its own sentence, which names the install.
    A command given up on at its deadline is (`docker.DockerTimedOutError`).
    Docker Desktop's engine while it starts or after it stopped is: the CLI
    reaches the pipe and is answered "request returned 500 Internal Server
    Error for API route and version http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/…"
    (older CLIs: no number, `docker_engine`), or by Docker 29 just after its
    engine VM died, "request returned 502 Bad Gateway for …" on the same route
    (PR 291's Windows live test). That is Desktop's proxy with no engine behind
    it, not the engine refusing, so it is told as not running and keeps the
    Open Docker Desktop press (T194 R3). On macOS the same answer names
    Desktop's socket under `~/.docker/run` (or `docker.raw.sock`).
    """
    if isinstance(exc, docker.DockerCliMissingError):
        return False
    if isinstance(exc, docker.DockerTimedOutError):
        return True
    said = str(exc).lower()
    if any(wording in said for wording in _UNREACHABLE_WORDINGS):
        return True
    return any(answer in said for answer in _DESKTOP_PROXY_ANSWERS) and any(
        pipe in said for pipe in _DESKTOP_ENGINE_PIPES
    )


def problem_of(exc: object, *, distro: str | None, deck_docker_removed: bool) -> Problem:
    """Which of the banner's failures `exc` is.

    A server in a WSL distro is that distro's Docker whatever it said; no CLI at
    all is `missing` (`removed` on a Deck an update took it from); a socket or
    pipe that refused us is `permission`; a command given up on at its
    deadline is `not-answering`; Docker not answering in a wording
    `unreachable()` knows is `not-running`; anything else is `unknown`, which
    never says Docker is down (T194 F2).
    """
    if distro is not None:
        return "wsl"
    if isinstance(exc, docker.DockerCliMissingError):
        return "removed" if deck_docker_removed else "missing"
    if isinstance(exc, docker.DockerTimedOutError):
        return "not-answering"
    said = str(exc).lower()
    if "permission denied" in said or "access is denied" in said:
        return "permission"
    if unreachable(exc):
        return "not-running"
    return "unknown"


def advise(problem: Problem, host: Host, *, distro: str | None = None) -> Advice:
    """The banner for `problem` on `host`."""
    desktop = host in ("windows", "macos")
    if problem == "wsl":
        body = _WSL_NOT_RUNNING.format(distro=distro) if distro else _WSL_UNNAMED
        return Advice(UNKNOWN_TITLE, body, None)
    if problem in ("missing", "removed"):
        if host == "deck":
            return Advice(UNKNOWN_TITLE, platform.STEAMOS_DOCKER_GONE_HELP, "reinstall-deck")
        if desktop:
            return Advice(UNKNOWN_TITLE, platform.DOCKER_MISSING_ON_DESKTOP, "open-desktop")
        return Advice(UNKNOWN_TITLE, platform.DOCKER_MISSING_ON_LINUX, None)
    if problem == "unknown":
        return Advice(UNKNOWN_TITLE, _UNKNOWN, None)
    if problem == "permission":
        if host == "windows":
            return Advice(UNKNOWN_TITLE, _WINDOWS_PERMISSION, None)
        if host == "macos":
            return Advice(UNKNOWN_TITLE, _MACOS_PERMISSION, None)
        if host == "deck":
            return Advice(UNKNOWN_TITLE, _DECK_PERMISSION, None)
        return Advice(UNKNOWN_TITLE, _ENGINE_PERMISSION, None)
    if desktop:
        # Not answering is told as not running: Docker Desktop's proxy hangs
        # with its engine VM gone, and opening Desktop is what brings it back
        # (PR 291's Windows live test).
        return Advice(UNKNOWN_TITLE, _DESKTOP_NOT_RUNNING, "open-desktop")
    if problem == "not-answering":
        body = _DECK_NOT_ANSWERING if host == "deck" else _ENGINE_NOT_ANSWERING
        return Advice(UNKNOWN_TITLE, body, None)
    if host == "deck":
        return Advice(UNKNOWN_TITLE, _DECK_NOT_RUNNING, None)
    return Advice(UNKNOWN_TITLE, _ENGINE_NOT_RUNNING, None)


def advice_for(
    exc: object,
    *,
    distro: str | None,
    host: Host | None = None,
    deck_docker_removed: bool | None = None,
) -> Advice:
    """The banner for a failed Docker read on this machine (or the one named).

    A distro WSL says is gone is told in `wsl.missing_distro_problem()`'s own
    sentence: "start Docker in it" would send the player to a distro that is
    not there.
    """
    where = host if host is not None else this_host()
    removed = (
        deck_docker_removed
        if deck_docker_removed is not None
        else platform.steamos_docker_removed()
    )
    if distro is not None and str(exc).startswith(f"The WSL distro {distro} no longer exists"):
        return Advice(UNKNOWN_TITLE, str(exc), None)
    return advise(problem_of(exc, distro=distro, deck_docker_removed=removed), where, distro=distro)
