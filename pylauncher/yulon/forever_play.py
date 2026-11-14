"""Play for WoW Forever: trust, account, config, start, key (`catalog.ForeverLogin`).

In order, on the worker thread the tab gives it:

1. The client folder is the 70707 client the realm accepts (`stack_install.client_problem`).
2. The world server is running: the account is made through its console.
3. This computer trusts the install's CA and maps the portal to 127.0.0.1
   (`forever_trust.ensure`): one administrator prompt, the first time only. On Linux (the
   Steam Deck) the CA is trusted inside the game's own Proton prefix instead, and the hosts
   line is added with `sudo -n` or named for the player to add (`forever_proton`).
4. The account (`forever_account.ensure`): made once, its password kept by Yu'lon.
5. `WTF/<config>` in the client's game folder: a copy of Config.wtf with this realm's
   `portal` and the account name filled in, so the login screen asks only for the
   password. Config.wtf itself is never written. This is the one file Play writes in the
   player's client folder.
6. The client, started with `-config <config>` (`play.args`), and the watch that keeps
   the server's login key in its memory while it runs (`forever_key`). On Linux the client
   runs under GE-Proton through Steam Linux Runtime 4 (`forever_proton`), and the key is
   written from Linux beside it (`forever_keywatch`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import (
    docker,
    forever_account,
    forever_key,
    forever_keywatch,
    forever_proton,
    forever_trust,
    play_launch,
    single_play,
)
from yulon.catalog.catalog import ForeverLogin, SingleEntry
from yulon.catalog.stack_install import client_problem
from yulon.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Played:
    """What the player needs to log in, once the game is starting."""

    account: str
    password: str
    asked_admin: bool


@dataclass(frozen=True)
class PlaySeams:
    """Everything Play reaches outside itself through. Real by default."""

    state_of: Callable[[str], docker.ContainerState] = docker.container_state
    trust: Callable[..., bool] = forever_trust.ensure
    account: Callable[..., tuple[str, str]] = forever_account.ensure
    start_client: Callable[[play_launch.LaunchSpec], bool] = single_play.start
    watch: Callable[..., object] = forever_key.start_watch
    hosts_ready: Callable[[str], bool] = forever_proton.hosts_ready
    prepare_proton: Callable[..., forever_proton.Runtime] = forever_proton.prepare
    start_game: Callable[..., None] = forever_proton.start_game
    keywatch: Callable[..., object] = forever_keywatch.start_watch


def login_of(entry: SingleEntry) -> ForeverLogin:
    """The entry's WoW Forever login; refuses an entry without one."""
    play = entry.play
    if play is None or play.forever is None:
        raise single_play.PlayRefusal(f"{entry.name} does not log in the WoW Forever way.")
    return play.forever


def world_port(entry: SingleEntry) -> int:
    """The world server's host port (`ports` role `world`): the key watch idles once on it."""
    return next((p.port for p in entry.ports if p.role == "world"), 8085)


def write_config(game_dir: Path, login: ForeverLogin, account: str) -> Path:
    """Write `WTF/<config>`: Config.wtf with this realm's portal and account name. Raises OSError.

    Lines of the player's own Config.wtf are kept as they are; only `portal` and
    `accountName` are set (added when missing), and a client with no English text data
    gets the first text locale it has rather than a forced enUS it would not start with.
    """
    wtf = game_dir / "WTF"
    target, base = wtf / login.config, wtf / "Config.wtf"
    source = target if target.is_file() else base if base.is_file() else None
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines() if source else []
    lines = _set(lines, "portal", login.portal)
    lines = _set(lines, "accountName", account)
    if not any(line.startswith("SET textLocale ") for line in lines):
        info = game_dir.parent / ".build.info"
        text = info.read_text(errors="replace") if info.is_file() else ""
        have = re.findall(r"\b([a-z]{2}[A-Z]{2}) text\?", text)
        lines.append(f'SET textLocale "{"enUS" if not have or "enUS" in have else have[0]}"')
    wtf.mkdir(parents=True, exist_ok=True)
    target.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return target


def _set(lines: list[str], key: str, value: str) -> list[str]:
    want = f'SET {key} "{value}"'
    if any(line.startswith(f"SET {key} ") for line in lines):
        return [want if line.startswith(f"SET {key} ") else line for line in lines]
    return [*lines, want]


def play(
    entry: SingleEntry,
    server_dir: Path,
    client_dir: Path | None,
    *,
    os_name: str,
    seams: PlaySeams | None = None,
    say: Callable[[str], None] = logger.info,
) -> Played:
    """Trust, account, config, start and key, in the module's order. Raises PlayRefusal."""
    s = seams if seams is not None else PlaySeams()
    login = login_of(entry)
    if os_name not in ("windows", "linux"):
        raise single_play.PlayRefusal(
            f"Play starts {entry.name} on Windows and on Linux (the Steam Deck) only."
        )
    stack = entry.install.stack
    extract = stack.client_extract if stack is not None else None
    if extract is not None:
        problem = client_problem(extract, client_dir)
        if problem is not None:
            raise single_play.PlayRefusal(problem)
    assert client_dir is not None and entry.play is not None
    world = forever_account.container_of(entry, forever_account.WORLD_SERVICE)
    if s.state_of(world).status != "running":
        raise single_play.PlayRefusal(
            f"Start {entry.name} first and wait until it is ready: Play makes your account "
            "through the running world server."
        )
    if os_name == "windows":
        try:
            asked = s.trust(server_dir / login.ca, login.portal)
        except forever_trust.TrustError as exc:
            raise single_play.PlayRefusal(str(exc)) from exc
    else:
        if not s.hosts_ready(login.portal):
            raise single_play.PlayRefusal(forever_proton.HOSTS_MISSING.format(portal=login.portal))
        asked = False
    try:
        account, password = s.account(entry, server_dir)
    except forever_account.AccountError as exc:
        raise single_play.PlayRefusal(str(exc)) from exc
    exe = client_dir / entry.play.exe
    try:
        write_config(exe.parent, login, forever_account.typed_name(login))
    except OSError as exc:
        raise single_play.PlayRefusal(
            f"{login.config} could not be written in {exe.parent / 'WTF'} "
            f"({exc.strerror or exc}), so the game was not started."
        ) from exc
    key = bytes.fromhex(login.public_key)
    if os_name == "windows":
        spec = single_play.launch_spec(
            entry, client_dir, host="127.0.0.1", account="", server_dir=server_dir, os_name=os_name
        )
        s.start_client(spec)
        s.watch(exe, key, login.region_group)
    else:
        try:
            runtime = s.prepare_proton(server_dir, client_dir, server_dir / login.ca, say)
            s.start_game(runtime, exe, entry.play.args)
        except forever_proton.ProtonError as exc:
            raise single_play.PlayRefusal(str(exc)) from exc
        s.keywatch(key, login.region_group, world_port(entry))
    logger.info(f"started {exe} for {account}")
    return Played(account=forever_account.typed_name(login), password=password, asked_admin=asked)


def login_text(entry: SingleEntry, played: Played) -> str:
    """What the dialog after Play says: the account, its password, and the first-login rule."""
    return (
        f"{entry.name} is starting. At its login screen, log in with\n\n"
        f"    {played.account}\n    password: {played.password}\n\n"
        "If a login fails, press Login again without closing the game: Yu'lon writes the "
        "server's login key into the game when you first press Login, and keeps it there."
    )
