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

import dataclasses
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from yulon import loader_account, platform, play_launch, single_client
from yulon.catalog import composegen
from yulon.catalog.catalog import LoaderLogin, SingleEntry
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
    # A WoW client asks for the account at its own login screen (`SinglePlay.account`).
    problem = check_account(account) if entry.play.account else None
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


LOADER_ON_WINDOWS_ONLY = (
    "Play starts {loader} on Windows only, for now. On Linux, start {loader} through Proton "
    "yourself, pointed at 127.0.0.1; a Steam Deck Play button for {name} comes next."
)


def loader_spec(
    entry: SingleEntry,
    server_dir: Path,
    *,
    host: str,
    os_name: str | None = None,
    find: Callable[[SingleEntry], single_client.FoundClient | None] | None = None,
) -> tuple[play_launch.LaunchSpec, Path]:
    """What Play starts for an entry whose client a loader starts (FFXI), and the client folder.

    The loader is the install's (`stack_install._fetch_loader()`), the client is the
    one the registry names (`single_client.find_client()`), and a client older than
    the pinned server accepts is refused here rather than at its login screen.
    """
    play = entry.play
    if play is None or play.loader is None:
        raise PlayRefusal(f"{entry.name} has no loader Yu'lon knows how to start.")
    loader = play.loader
    here = os_name if os_name is not None else platform.detect()
    if here != "windows":
        raise PlayRefusal(LOADER_ON_WINDOWS_ONLY.format(loader=loader.name, name=entry.name))
    found = (find or (lambda e: single_client.find_client(e, os_name=here)))(entry)
    if found is None:
        raise PlayRefusal(single_client.NOT_FOUND.format(name=entry.name))
    if not (found.folder / play.exe).is_file():
        raise PlayRefusal(
            f"The registry names {found.folder} as your {entry.name} client, and there is no "
            f"{play.exe} in it. Reinstall it with Square Enix's installer, then press Play again."
        )
    problem = single_client.version_problem(entry, found.folder)
    if problem is not None:
        raise PlayRefusal(problem)
    exe = server_dir / loader.dest
    if not exe.is_file():
        raise PlayRefusal(
            f"{loader.name} is missing from {exe.parent}. Press Install on the Catalog tab and "
            "choose this server's folder: it fetches it again and changes nothing else."
        )
    tokens = {**entry.port_tokens(), "HOST": host, "CLIENT_LANG": found.lang}
    args = tuple(composegen.fill(arg, tokens) for arg in loader.args)
    return play_launch.LaunchSpec(argv=(str(exe), *args), env={}, cwd=exe.parent), found.folder


def check_loader_account(entry: SingleEntry, account: str) -> str | None:
    """Why `account` cannot be signed in through the loader, or None. Empty is allowed.

    An empty name is the loader's own prompt: an account made there, whose password
    Yu'lon never had (`loader_account`), is still played through Play.
    """
    login = _login_of(entry)
    if not account or login is None:
        return None
    return loader_account.check_name(login, account)


def sign_in(
    entry: SingleEntry,
    host: str,
    account: str,
    *,
    config_dir: Path | None = None,
    ensure: Callable[..., str] = loader_account.ensure,
) -> Path:
    """Make sure `account` is on the server, and write the loader's login file; its path.

    Worker thread: one request to the server's auth port (`loader_account.ensure`).
    """
    login = _login_of(entry)
    if login is None:
        raise PlayRefusal(f"{entry.name} has no sign-in Yu'lon knows how to do.")
    problem = loader_account.check_name(login, account)
    if problem is not None:
        raise PlayRefusal(problem)
    assert entry.play is not None and entry.play.loader is not None
    try:
        password = ensure(
            entry.id,
            login,
            host=host,
            port=entry.port(login.port),
            name=account,
            loader=entry.play.loader.name,
            config_dir=config_dir,
        )
        return loader_account.write_login_file(
            entry.id, login, name=account, password=password, config_dir=config_dir
        )
    except loader_account.AccountRefusal as exc:
        raise PlayRefusal(str(exc)) from exc
    except OSError as exc:
        raise PlayRefusal(
            f"Yu'lon could not write {entry.play.loader.name}'s login file: {exc.strerror or exc}"
        ) from exc


def with_login(
    entry: SingleEntry, spec: play_launch.LaunchSpec, login_file: Path
) -> play_launch.LaunchSpec:
    """`spec` with the arguments that tell the loader to sign in from `login_file`."""
    login = _login_of(entry)
    if login is None:
        return spec
    args = tuple(composegen.fill(arg, {"LOGIN_FILE": str(login_file)}) for arg in login.args)
    return dataclasses.replace(spec, argv=(*spec.argv, *args))


def _login_of(entry: SingleEntry) -> LoaderLogin | None:
    play = entry.play
    if play is None or play.loader is None:
        return None
    return play.loader.login


def start_loader(
    entry: SingleEntry,
    spec: play_launch.LaunchSpec,
    client: Path,
    *,
    grant: Callable[[SingleEntry, Path], single_client.GrantReport] = single_client.grant,
    launch: Callable[[play_launch.LaunchSpec], None] = play_launch.launch_console,
) -> bool:
    """Make the game's settings folders the player's (once), then start the loader. Worker thread.

    True when Windows asked the player for administrator rights on the way: the first
    Play of a client under Program Files. A declined or failed grant starts nothing,
    because the game would only stop at FFXI-3117 after the player logged in.
    """
    report = grant(entry, client)
    if not report.ok:
        raise PlayRefusal("\n\n".join(report.problems))
    try:
        launch(spec)
    except OSError as exc:
        raise PlayRefusal(f"{spec.argv[0]} could not be started: {exc.strerror or exc}") from exc
    return bool(report.granted)


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
