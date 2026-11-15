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
class TrustRecord:
    """The install record's trust half (`state.KnownInstall`), as Play reads and writes it.

    `stale` names the CA thumbprints still to be deleted from this computer's store;
    `remember_stale` persists one BEFORE a remake replaces the certificate it is of
    (T888), so a declined prompt cannot strand it; `forget_stale` drops one once the store
    says it is gone; `trusted` records the new CA's thumbprint, and the trust step calls it
    only after the batch and the post-check have succeeded. The defaults are a record with
    nothing in it, for a Play that is not driven by a window.
    """

    stale: Callable[[], Sequence[str]] = lambda: ()
    remember_stale: Callable[[str], None] = lambda _thumb: None
    forget_stale: Callable[[str], None] = lambda _thumb: None
    trusted: Callable[[str], None] | None = None


@dataclass(frozen=True)
class PlaySeams:
    """Everything Play reaches outside itself through. Real by default."""

    state_of: Callable[[str], docker.ContainerState] = docker.container_state
    trust: Callable[..., bool] = forever_trust.ensure
    trust_record: TrustRecord = TrustRecord()
    """Where the trusted CA's thumbprint is recorded (`state.KnownInstall`), for removal."""
    remake_certificates: Callable[[SingleEntry, Path], None] = stack_install.remake_certificates
    """Re-render the compose files, then make the certificates again (T888)."""
    restart_service: Callable[..., None] = docker.compose_up_service
    """Restart the login server after its certificates changed (`--force-recreate`)."""
    container_project: Callable[[str], str | None] = docker.container_project
    """Which compose project owns a container, so a same-named container is never restarted."""
    compose_project: Callable[[SingleEntry, Path], str] = stack_install.project_of
    """This install's own compose project name, to compare the container's owner against."""
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

    Before anything is replaced the old CA's thumbprint is written to the install record
    (T888): the old root stays in this computer's store until a `certutil -delstore`
    succeeds, and that name must survive a declined prompt or a Yu'lon closed at the wrong
    moment. The stack's own script then makes the new set, in a staging folder inside the
    container, and swaps it in only after `openssl verify` -- nothing in `certs/` is
    deleted from here, where rootful Docker owns the folder. Only the login server reads
    the new certificate at its start, and it is restarted only when it was running AND the
    container of that name belongs to THIS install's compose project (a fixed container
    name is not proof of ownership). One INFO line, and no prompt beyond the one the trust
    step asks for anyway.

    Raises `PlayRefusal` when the certificate or a restart fails; a failed script leaves
    the old set untouched and Play refuses with its sentence.
    """
    ca = server_dir / login.ca
    old = ""
    try:
        old = forever_trust.thumbprint(ca) if ca.is_file() else ""
    except forever_trust.TrustError:
        old = ""
    if old and old not in seams.trust_record.stale():
        try:
            seams.trust_record.remember_stale(old)
        except OSError as exc:
            raise single_play.PlayRefusal(
                f"The certificate this server used before could not be remembered "
                f"({exc.strerror or exc}), so it was not replaced: a certificate this "
                "computer trusts must not be lost track of. Press Play again."
            ) from exc
    try:
        seams.remake_certificates(entry, server_dir)
    except (CertificatesError, docker.DockerCommandError) as exc:
        raise single_play.PlayRefusal(str(exc)) from exc
    restart = _restart_login(entry, server_dir, seams)
    logger.info(
        "remade the server's certificates; restarted " + (_LOGIN_SERVICE if restart else "nothing")
    )
    say("This server's certificate was made again for its own login name.")
    return old


_LOGIN_SERVICE = "bnet"
"""The one service that reads the certificate at start: the Battle.net login server."""


def _restart_login(entry: SingleEntry, server_dir: Path, seams: PlaySeams) -> bool:
    """Restart the login server if it is running and this install owns it; whether it did.

    `--force-recreate` makes a new container that re-reads `certs/`, and only the login
    server needs it (T888): the world server does not use the certificate, and recreating
    it used to race the account step that runs right after. A container OF THAT NAME in
    another compose project (a second install, an abandonware copy) is not this install's,
    and is left alone.
    """
    container = forever_account.container_of(entry, _LOGIN_SERVICE)
    if seams.state_of(container).status != "running":
        return False
    owner = seams.container_project(container)
    project = seams.compose_project(entry, server_dir)
    if owner != project:
        logger.warning(
            f"not restarting {container} with the new certificate: it belongs to "
            + (f"the compose project {owner!r}, not {project!r}" if owner else "no compose project")
        )
        return False
    try:
        seams.restart_service(server_dir, _LOGIN_SERVICE, force_recreate=True)
    except docker.DockerCommandError as exc:
        raise single_play.PlayRefusal(
            f"The {_LOGIN_SERVICE} server could not be restarted with the new certificate: {exc}"
        ) from exc
    return True


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
    if stack is not None and stack.certificates:
        reason = certificate_remake_reason(server_dir, login)
        if reason is not None:
            remake_certificates(entry, server_dir, login, seams=s, say=say)
    if os_name == "windows":
        try:
            asked = s.trust(
                server_dir / login.ca,
                login.portal,
                record=s.trust_record.trusted,
                stale=s.trust_record.stale(),
                forget=s.trust_record.forget_stale,
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
    stale: Sequence[str] = (),
    strip_hosts: bool = True,
    seams: forever_trust.TrustSeams | None = None,
) -> tuple[str, ...]:
    """Take this install's login trust off the computer; the comments left for Details.

    The CA's thumbprint comes from the install record when there is one, so this works
    after the certs folder (and the certificate) is gone; `certs/ca.pem` is the fallback
    for an install recorded before the thumbprint was kept. `stale` are the CAs this
    install replaced and has not seen leave the store; `strip_hosts` is False while
    another WoW Forever install on this computer still needs the one tagged hosts line
    (T888), and then nothing prompts unless there is a certificate to delete. Nothing here
    raises: the caller is dropping a record, and a refused prompt must not keep a server
    the player asked to remove.
    """
    login = login_of(entry)
    try:
        return forever_trust.remove(
            ca=server_dir / login.ca,
            thumb=thumbprint,
            stale=stale,
            strip_hosts=strip_hosts,
            seams=seams,
        )
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
