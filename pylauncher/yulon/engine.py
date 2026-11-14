"""Which container engine this process talks to: Docker, or rootless Podman (T887).

Phase 1 is a dark seam behind the developer switch `YULON_ENGINE=podman`. Without
the switch every answer here is Docker's and nothing changes. The client stays
the Docker CLI + Compose; under Podman it reaches the engine through
`DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock`.

`resolve()` is asked once per process and cached there, because the answer must
not change underneath a running install. Nothing else decides.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from yulon.log import get_logger

logger = get_logger(__name__)

Engine = Literal["docker", "podman"]
"""Which engine answers this process's `docker` commands."""

ENGINE_ENV_VAR = "YULON_ENGINE"
"""The developer switch naming the engine (T887 phase 1). Anything else is Docker."""

PODMAN_SWITCH_VALUE = "podman"

_resolved: Engine | None = None
"""The process's engine, once asked. `None` is "not asked yet", never an engine."""


def _switch_says_podman(environ: dict[str, str] | None = None) -> bool:
    """Whether the developer switch names Podman."""
    env = environ if environ is not None else os.environ
    return env.get(ENGINE_ENV_VAR) == PODMAN_SWITCH_VALUE


def _on_supported_platform(sys_platform: str | None = None, *, wsl: bool | None = None) -> bool:
    """Whether Podman may be chosen on this machine: real Linux, not WSL."""
    from yulon import platform

    current = sys_platform if sys_platform is not None else sys.platform
    if current != "linux":
        return False
    return not (wsl if wsl is not None else platform.in_wsl())


def resolve(
    environ: dict[str, str] | None = None,
    sys_platform: str | None = None,
    *,
    wsl: bool | None = None,
) -> Engine:
    """Which engine this process talks to, asked once and cached afterwards.

    Podman only when the developer switch says so AND this is real Linux, not
    WSL: `sys.platform` must be `"linux"` and `platform.in_wsl()` False. Every
    other machine is Docker, switch or no switch. The seams exist so a test
    pins each rule without running on that machine.
    """
    global _resolved
    if _resolved is not None:
        return _resolved
    engine: Engine = (
        "podman"
        if _switch_says_podman(environ) and _on_supported_platform(sys_platform, wsl=wsl)
        else "docker"
    )
    _resolved = engine
    return engine


def _reset_for_tests() -> None:
    """Forget the cached answer. Tests only; production asks once per process."""
    global _resolved
    _resolved = None


def is_podman() -> bool:
    """Whether this process talks to rootless Podman rather than Docker."""
    return resolve() == "podman"


def podman_socket(uid: int | None = None, runtime_dir: str | None = None) -> Path:
    """Where rootless Podman's API socket lives for this user.

    `$XDG_RUNTIME_DIR/podman/podman.sock`, falling back to
    `/run/user/<uid>/podman/podman.sock`. The seams (both default to this
    process's own answer) exist so a test pins the fallback without becoming
    another user.
    """
    directory = runtime_dir if runtime_dir is not None else os.environ.get("XDG_RUNTIME_DIR")
    if directory:
        return Path(directory) / "podman" / "podman.sock"
    who = uid if uid is not None else os.getuid()
    return Path(f"/run/user/{who}/podman/podman.sock")


def apply_docker_host(environ: dict[str, str] | None = None) -> None:
    """Point the Docker CLI at Podman when the engine is Podman (T887).

    Sets `DOCKER_HOST` to the Podman socket's `unix://` URL, once, in this
    process's environment so every child (`runner.run`, build envs built from
    `os.environ`) inherits it. A `DOCKER_HOST` the user already set is never
    overwritten: Yu'lon then uses whatever it points at. Off Podman this does
    nothing at all.
    """
    env = environ if environ is not None else os.environ
    if resolve() != "podman":
        return
    if env.get("DOCKER_HOST"):
        logger.info("DOCKER_HOST is already set; leaving it alone")
        return
    env["DOCKER_HOST"] = f"unix://{podman_socket()}"


def client_dir(home: Path | None = None) -> Path:
    """Where Yu'lon keeps the Docker CLI it downloads for Podman hosts.

    `~/.local/share/yulon/bin` (phase 2 downloads the `docker` binary there).
    The seam is the home directory, so a test does not touch the real one.
    """
    root = home if home is not None else Path.home()
    return root / ".local" / "share" / "yulon" / "bin"
