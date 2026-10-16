"""Play for a single-container server: start the player's own client, pointed at this server.

DDON's client takes everything on its command line — the login server's address and
port, the web server it downloads from, and a token the server treats as the account
name, making the account the first time it sees one (`LoginServerSetting.AccountRequired`
is false out of the box). So Play writes nothing into the client folder: it fills the
catalog's argument templates (`catalog.SinglePlay.args`) and starts the executable,
through `play_launch.program_launch_spec()` on every OS.

The client is the player's own (README §3a); this module only ever reads its folder.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from yulon import platform, play_launch
from yulon.catalog import composegen
from yulon.catalog.catalog import SingleEntry
from yulon.log import get_logger

logger = get_logger(__name__)

ACCOUNT_NAME = re.compile(r"^[A-Za-z0-9_]{3,20}$")
"""What an account name may be: letters, digits and `_`, three to twenty of them.

Narrower than the server checks, on purpose. The name travels as the client's
one-time token on its command line, where a space or a quote would split or end the
argument; upstream's own example token is twenty characters, and nothing longer has
been measured to reach the server intact.
"""

ERROR_ELEVATION_REQUIRED = 740
"""Windows' "The requested operation requires elevation" (`ERROR_ELEVATION_REQUIRED`)."""


class PlayRefusal(RuntimeError):
    """Play cannot start the client; the message says what to do next, and nothing started."""


def check_account(name: str) -> str | None:
    """Why `name` cannot be used as an account name, or None."""
    if not ACCOUNT_NAME.match(name):
        return (
            "An account name is 3 to 20 letters, digits or underscores, with no spaces. "
            "The account is made the first time you log in with it."
        )
    return None


def arguments(entry: SingleEntry, *, host: str, account: str) -> tuple[str, ...]:
    """The client's arguments for this server: the catalog's templates, filled in."""
    if entry.play is None:
        raise PlayRefusal(f"{entry.name} has no client Yu'lon knows how to start.")
    tokens = {**entry.port_tokens(), "HOST": host, "ACCOUNT": account}
    return tuple(composegen.fill(arg, tokens) for arg in entry.play.args)


def launch_spec(
    entry: SingleEntry,
    client_dir: Path | None,
    *,
    host: str,
    account: str,
    server_dir: Path,
    os_name: str | None = None,
    home: Path | None = None,
    config_dir: Path | None = None,
) -> play_launch.LaunchSpec:
    """What Play starts for `entry`, or a refusal saying what is missing."""
    if entry.play is None:
        raise PlayRefusal(f"{entry.name} has no client Yu'lon knows how to start.")
    if client_dir is None:
        raise PlayRefusal(
            f"Choose {entry.play.client} first. Yu'lon never downloads a client; it starts "
            "the one you have."
        )
    exe = client_dir / entry.play.exe
    if not exe.is_file():
        raise PlayRefusal(
            f"There is no {entry.play.exe} in {client_dir}, so nothing was started. Choose "
            f"{entry.play.client}."
        )
    problem = check_account(account)
    if problem is not None:
        raise PlayRefusal(problem)
    name = entry.name
    try:
        return play_launch.program_launch_spec(
            exe,
            arguments(entry, host=host, account=account),
            game=entry.id,
            server_dir=server_dir,
            os_name=os_name if os_name is not None else platform.detect(),
            home=home if home is not None else Path.home(),
            config_dir=config_dir if config_dir is not None else platform.config_dir(),
            no_runner_linux=play_launch.NO_RUNNER_LINUX_FOR.format(exe=exe.name, name=name),
            no_runner_macos=play_launch.NO_RUNNER_MACOS_FOR.format(exe=exe.name, name=name),
        )
    except play_launch.LaunchRefusal as exc:
        raise PlayRefusal(str(exc)) from exc


StartElevated = Callable[[play_launch.LaunchSpec], None]


def _start_elevated(spec: play_launch.LaunchSpec) -> None:
    """Start `spec` through Windows' "Run as administrator", which asks the player (UAC)."""
    if sys.platform != "win32":
        raise OSError("administrator rights are asked for on Windows only")
    exe, *args = spec.argv
    os.startfile(exe, "runas", " ".join(f'"{arg}"' for arg in args), str(spec.cwd))


def start(
    spec: play_launch.LaunchSpec,
    *,
    os_name: str | None = None,
    launch: Callable[[play_launch.LaunchSpec], None] = play_launch.launch,
    start_elevated: StartElevated = _start_elevated,
) -> bool:
    """Start the client; True if Windows had to ask the player for administrator rights.

    The DDON launcher asks for them because the client's executable can demand them.
    Such a program cannot be started by a plain process spawn (`ERROR_ELEVATION_REQUIRED`),
    so on that one answer, and only on Windows, it is started again through the UAC
    prompt; the player sees Windows' own question and may say no.
    """
    try:
        launch(spec)
        return False
    except OSError as exc:
        here = os_name if os_name is not None else platform.detect()
        if here == "windows" and getattr(exc, "winerror", None) == ERROR_ELEVATION_REQUIRED:
            logger.info(f"{spec.argv[0]} needs administrator rights; asking Windows for them")
            try:
                start_elevated(spec)
            except OSError as again:
                raise PlayRefusal(
                    "The client needs administrator rights, and Windows did not start it "
                    f"({again.strerror or again})."
                ) from again
            return True
        raise PlayRefusal(f"The client could not be started: {exc.strerror or exc}") from exc
