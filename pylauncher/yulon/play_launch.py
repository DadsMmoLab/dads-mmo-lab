"""T181a — what Play runs, per OS, and how it is started so it outlives Yu'lon.

Two halves, kept apart on purpose. `launch_spec()` DECIDES: it answers the argv,
the extra environment and the working folder, or refuses with a sentence that
says what to install or do. `launch()` STARTS what it was given and decides
nothing. The deciding half is where every refusal lives and is tested as a
plain value; the starting half is the one line that can put a game window on a
test box, and the suite refuses it outright (`tests/conftest.py`).

Per OS (owner decision 3, 2026-09-30):

* **Windows** runs `Wow.exe` itself.
* **Linux and the Steam Deck** run it through Proton directly — `proton run
  Wow.exe` — with no Steam client in the loop, then fall back to a system
  `wine`. The Proton is the one Add to Steam… would name
  (`steam.find_proton_script`, same order as `find_compat_tool`), so the two
  buttons never start the same client under two different Protons.
* **macOS** runs it through `wine` if there is one. Proton is Linux-only.

Two things about Proton that are not obvious:

* **`STEAM_COMPAT_DATA_PATH` is a Wine prefix, and it holds state.** The
  registry, the fake `C:` drive, whatever the client writes outside its own
  folder. One prefix per server, keyed on a sha256 of the server folder: a
  shared one leaks one server's settings into another's, and Python's `hash()`
  is salted per process, so a prefix named by it would be a new, empty prefix
  every time Yu'lon starts.
* **`STEAM_COMPAT_CLIENT_INSTALL_PATH` is the Steam root**, the parent of
  `userdata` — the folder `steam.steam_roots()` answers the `userdata` of.
  Proton reads Steam's own libraries from there; without it `proton run`
  stops before the game starts.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yulon import platform, runner, steam

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
"""Win32 process-creation flags, spelled as numbers so this module type-checks off Windows.

`DETACHED_PROCESS` is right for `Wow.exe` and was wrong for the self-update
helper (`selfupdate.swap.windows_creation_flags`): that one is `powershell.exe`,
a console program that exits at once with no console at all. WoW is a GUI
program and has no use for the console it would otherwise inherit.
"""


@dataclass(frozen=True)
class LaunchSpec:
    """What Play starts: the argv, the variables ADDED to the environment, the folder.

    `env` holds only what this module adds; `launch()` lays it over a copy of
    the environment Yu'lon itself has, so a spec reads as the difference it makes.
    """

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path


class LaunchRefusal(RuntimeError):
    """Play cannot start the game; the message says what to do next, and nothing started."""


NO_WOW_EXE = (
    "There is no Wow.exe in the ready-to-play client at {folder}, so there is "
    "nothing to start and nothing was started. Use Refresh from your original "
    "client to copy it back from your own client folder, then press Play again."
)

NO_RUNNER_LINUX = (
    "Nothing on this machine can run Wow.exe, so nothing was started. WoW is a "
    "Windows program and needs Proton or Wine here. Install Proton through Steam "
    "(Library → filter by Tools → Proton → Install), or install Wine from your "
    "distribution's packages, then press Play again."
)

NO_RUNNER_MACOS = (
    "Nothing on this Mac can run Wow.exe, so nothing was started. WoW is a "
    "Windows program and needs Wine on macOS. Install Wine (for example through "
    "Homebrew), then press Play again."
)

NO_PREFIX = (
    "The Proton folder for this server, {prefix}, could not be created ({reason}), "
    "so nothing was started. Check that {parent} is writable, then press Play again."
)


def launch_spec(
    play_dir: Path,
    *,
    game: str,
    server_dir: Path,
    os_name: str,
    home: Path,
    config_dir: Path,
    which: Callable[[str], str | None] = shutil.which,
) -> LaunchSpec:
    """How to start the ready-to-play client in `play_dir` on `os_name`, or a refusal.

    `os_name` is `platform.detect()`'s answer, passed in rather than read here
    so every OS's command is a plain value the suite can ask for on any box.

    The binary is checked first, on every OS: a runner handed a path that is not
    there starts, fails, and says so in a log nobody reads, while the player
    watches nothing happen.
    """
    exe = steam.client_executable(play_dir)
    if not exe.is_file():
        raise LaunchRefusal(NO_WOW_EXE.format(folder=play_dir))
    if os_name == "windows":
        return LaunchSpec(argv=(str(exe),), env={}, cwd=play_dir)
    if os_name != "macos":
        found = _proton(home)
        if found is not None:
            script, steam_root = found
            prefix = _prefix(config_dir, game, server_dir)
            return LaunchSpec(
                argv=(str(script), "run", str(exe)),
                env={
                    "STEAM_COMPAT_DATA_PATH": str(prefix),
                    "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam_root),
                },
                cwd=play_dir,
            )
    wine = which("wine")
    if wine:
        return LaunchSpec(argv=(wine, str(exe)), env={}, cwd=play_dir)
    raise LaunchRefusal(NO_RUNNER_MACOS if os_name == "macos" else NO_RUNNER_LINUX)


def _proton(home: Path) -> tuple[Path, Path] | None:
    """The first Proton script of the first Steam that has one: `(script, steam root)`.

    Native first, then the Flathub Steam, in `steam.steam_roots()` order. The
    root is taken whether or not `userdata` exists under it: Steam installs its
    Protons before anyone signs in, and Play needs no profile. De-duplicated by
    `resolve()` because `~/.steam/steam` is usually the native root again
    through a symlink — harmless here, but it would be asked twice.
    """
    seen: set[Path] = set()
    for userdata in steam.steam_roots(home):
        root = userdata.parent
        if not root.is_dir() or root.resolve() in seen:
            continue
        seen.add(root.resolve())
        script = steam.find_proton_script(root)
        if script is not None:
            return script, root
    return None


def _prefix(config_dir: Path, game: str, server_dir: Path) -> Path:
    """This server's own Proton prefix under Yu'lon's data folder, created if absent.

    Named `<game>-<first 12 hex of sha256(server folder)>`: stable across runs,
    unlike `hash()`, and readable enough to find by hand. The game id is kept to
    file-name characters so an id with a slash in it cannot climb out of
    `proton/`. Proton makes the prefix's contents on the first run; the folder
    has to exist before that.
    """
    digest = hashlib.sha256(str(server_dir).encode("utf-8")).hexdigest()[:12]
    parent = config_dir / "proton"
    prefix = parent / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', game)}-{digest}"
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LaunchRefusal(
            NO_PREFIX.format(prefix=prefix, reason=exc.strerror or exc, parent=parent)
        ) from exc
    return prefix


Popen = Callable[..., Any]

_spawn: Popen = subprocess.Popen
"""The real spawn behind `launch()`, looked up per call so the suite can refuse it
(`tests/conftest.py`): a default bound in the signature would be `Popen` itself,
and a test that forgot `popen=` would start a real game on the box running it."""


def launch(spec: LaunchSpec, *, popen: Popen | None = None) -> None:
    """Start `spec` so that it OUTLIVES Yu'lon: closing the app never closes WoW.

    POSIX: `start_new_session=True`, so the terminal's SIGHUP and this process's
    exit do not reach it. Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`,
    so it has no console of ours and a Ctrl-C in our group is not its.

    stdio goes to `DEVNULL` rather than to pipes nobody reads: Wine writes a
    great deal, and a full pipe would stall the game once Yu'lon stopped
    draining it — or once Yu'lon was gone.

    The environment is a NEW dict — `runner.child_env()` (so a frozen build's
    `LD_LIBRARY_PATH` does not reach Proton's Python or Wine) with the spec's
    variables over it. `os.environ` is never updated: that would point every
    later launch, Proton or not, at the last server's prefix.
    """
    start = popen if popen is not None else _spawn
    env = dict(runner.child_env() or os.environ)
    env.update(spec.env)
    detached: dict[str, Any]
    if platform.detect() == "windows":
        detached = {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP}
    else:
        detached = {"start_new_session": True}
    start(
        list(spec.argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(spec.cwd),
        env=env,
        **detached,
    )
