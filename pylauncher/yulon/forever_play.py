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
from collections.abc import Callable, Sequence
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
from yulon.catalog import stack_install
from yulon.catalog.catalog import ForeverLogin, SingleEntry
from yulon.catalog.stack_install import CertificatesError, client_problem
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
    remember_trust: Callable[[str], None] | None = None
    """Where the trusted CA's thumbprint is recorded (`state.KnownInstall`), for removal."""
    remake_certificates: Callable[[Path], None] = stack_install.make_certificates
    """Make the install's certificates again, in the stack's own image (T888)."""
    restart_service: Callable[..., None] = docker.compose_up_service
    """Restart one service of the install after its certificates changed (`--force-recreate`)."""
    account: Callable[..., tuple[str, str]] = forever_account.ensure
    start_client: Callable[[play_launch.LaunchSpec], bool] = single_play.start
    watch: Callable[..., object] = forever_key.start_watch
    game_pids: Callable[[Path], Sequence[int]] = forever_key.pids_under
    find_game: Callable[[Path], int | None] = lambda client: forever_keywatch.game_pid(
        Path("/proc"), client
    )
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


ALREADY_RUNNING = "WoW Forever is already running. Close it, then press Play again."
"""What Play refuses with while this client's game runs (T888)."""

CERTIFICATE_FILES: tuple[str, ...] = (
    "ca.key",
    "ca.pem",
    "server.key",
    "server.pem",
    "chain.pem",
)
"""Everything the old installer's script and this one write, removed before a remake.

`ca.key` is on the list although Yu'lon's own script never writes it there: the image's
`certs.sh` did, in a folder the player can write to, and an install made by the old one
has to lose it.
"""

OLD_CERTS_REASON = (
    "the certificates were made by the older script, whose CA also vouched for Blizzard's "
    "real actual.battle.net hosts and left its key in the install folder"
)


def certificate_remake_reason(server_dir: Path, login: ForeverLogin) -> str | None:
    """Why this install's certificates must be made again before Play trusts them; None.

    Three answers: `certs/ca.key` exists (the image's script left the CA's private key in a
    folder anybody can read), `ca.pem` is missing, or `ca.pem` is not the CA Yu'lon's own
    script makes (`forever_trust.certificate_problem`: CA:TRUE, a critical name constraint,
    and that constraint one name).
    """
    ca = server_dir / login.ca
    if (ca.parent / "ca.key").exists():
        return OLD_CERTS_REASON
    if not ca.is_file():
        return "there is no ca.pem in the install folder yet"
    return forever_trust.certificate_problem(ca, login.portal)


def remake_certificates(
    entry: SingleEntry,
    server_dir: Path,
    login: ForeverLogin,
    *,
    seams: PlaySeams,
    say: Callable[[str], None],
) -> str:
    """Make the install's certificates again; the old CA's thumbprint, or "".

    The old files go first (a script that finds `chain.pem` and friends says "ready" and
    leaves them), and with them the CA's key the old script left behind. The stack's own
    image makes the new ones; the login and world services are restarted only if they were
    running, since they read the certificate at start. One INFO line, and no prompt beyond
    the one the trust step asks for anyway.

    Raises `PlayRefusal` when the script or a restart fails.
    """
    ca = server_dir / login.ca
    stale = ""
    try:
        stale = forever_trust.thumbprint(ca) if ca.is_file() else ""
    except forever_trust.TrustError:
        stale = ""
    for name in CERTIFICATE_FILES:
        try:
            (ca.parent / name).unlink(missing_ok=True)
        except OSError as exc:
            raise single_play.PlayRefusal(
                f"The old certificate {ca.parent / name} could not be removed "
                f"({exc.strerror or exc}), so the certificate was not remade."
            ) from exc
    try:
        seams.remake_certificates(server_dir)
    except (CertificatesError, docker.DockerCommandError) as exc:
        raise single_play.PlayRefusal(str(exc)) from exc
    restarted = [
        service
        for service in _listener_services(entry)
        if seams.state_of(forever_account.container_of(entry, service)).status == "running"
    ]
    for service in restarted:
        try:
            seams.restart_service(server_dir, service, force_recreate=True)
        except docker.DockerCommandError as exc:
            raise single_play.PlayRefusal(
                f"The {service} server could not be restarted with the new certificate: {exc}"
            ) from exc
    logger.info(
        "remade the server's certificates; restarted "
        + (", ".join(restarted) if restarted else "nothing")
    )
    say("This server's certificate was made again for its own login name.")
    return stale


def _listener_services(entry: SingleEntry) -> tuple[str, ...]:
    """The services that read the certificate at start: the login server and the world."""
    stack = entry.install.stack
    named = {service.service for service in stack.services} if stack is not None else set()
    return tuple(s for s in ("bnet", forever_account.WORLD_SERVICE) if s in named)


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
    exe = client_dir / entry.play.exe
    # One game per Play (T888): the key watch writes into a process's memory, and the game
    # it must write into is the one this press starts, never the one already running.
    before: tuple[int, ...] = ()
    if os_name == "windows":
        before = tuple(s.game_pids(client_dir))
        if before:
            raise single_play.PlayRefusal(ALREADY_RUNNING)
    elif s.find_game(client_dir) is not None:
        raise single_play.PlayRefusal(ALREADY_RUNNING)
    world = forever_account.container_of(entry, forever_account.WORLD_SERVICE)
    if s.state_of(world).status != "running":
        raise single_play.PlayRefusal(
            f"Start {entry.name} first and wait until it is ready: Play makes your account "
            "through the running world server."
        )
    stale = ""
    if stack is not None and stack.certificates:
        reason = certificate_remake_reason(server_dir, login)
        if reason is not None:
            stale = remake_certificates(entry, server_dir, login, seams=s, say=say)
    if os_name == "windows":
        try:
            asked = s.trust(
                server_dir / login.ca,
                login.portal,
                record=s.remember_trust,
                stale=stale or None,
            )
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
        s.watch(exe, key, login.region_group, before=before)
    else:
        try:
            runtime = s.prepare_proton(
                server_dir, client_dir, server_dir / login.ca, say, portal=login.portal
            )
            s.start_game(runtime, exe, entry.play.args)
        except (forever_proton.ProtonError, forever_trust.TrustError) as exc:
            raise single_play.PlayRefusal(str(exc)) from exc
        s.keywatch(key, login.region_group, world_port(entry), client_dir=client_dir, before=before)
    logger.info(f"started {exe} for {account}")
    return Played(account=forever_account.typed_name(login), password=password, asked_admin=asked)


def remove_trust(
    entry: SingleEntry,
    server_dir: Path,
    *,
    thumbprint: str | None = None,
    seams: forever_trust.TrustSeams | None = None,
) -> tuple[str, ...]:
    """Take this install's login trust off the computer; the comments left for Details.

    The CA's thumbprint comes from the install record when there is one, so this works
    after the certs folder (and the certificate) is gone; `certs/ca.pem` is the fallback
    for an install recorded before the thumbprint was kept. Nothing here raises: the
    caller is dropping a record, and a refused prompt must not keep a server the player
    asked to remove.
    """
    login = login_of(entry)
    try:
        return forever_trust.remove(ca=server_dir / login.ca, thumb=thumbprint, seams=seams)
    except OSError as exc:
        logger.warning(f"could not take {entry.name}'s trust off this computer: {exc}")
        return ()


def login_text(entry: SingleEntry, played: Played) -> str:
    """What the dialog after Play says: the account, its password, and the first-login rule."""
    return (
        f"{entry.name} is starting. At its login screen, log in with\n\n"
        f"    {played.account}\n    password: {played.password}\n\n"
        "If a login fails, press Login again without closing the game: Yu'lon writes the "
        "server's login key into the game when you first press Login, and keeps it there."
    )
