"""WoW Forever's login trust: this computer trusts the install's CA, and hosts names its server.

The 70707 client dials the Battle.net name in its config's portal over TLS and checks the
certificate against the computer's trusted roots. A WoW Forever install makes its own CA
before its first start (Yu'lon's `certs-yulon.sh`, from the install's stack files): its
`nameConstraints` allow exactly `trinity.actual.battle.net` and nothing else, and its key
lives in the one-shot container's temporary folder, never in the install folder. That
constraint is the whole of what makes the CA safe to put in a machine's trust store, so
this module CHECKS it (`x509_names`) instead of taking the file's word for it, and it
checks the bytes it then installs. Play therefore needs, once per computer:

* the CA in `LocalMachine\\Root` (`certutil -addstore Root`), and
* `127.0.0.1 <portal>` in the hosts file, so the name reaches this computer's server.

Both need an administrator, so they go behind ONE UAC prompt
(`platform.elevated_admin_batch()`), and only what is missing is done. Reading is
unelevated and has no side effects.

`remove()` is the other half, and it runs when the server is removed from Yu'lon or
uninstalled: the CA out of `LocalMachine\\Root` by the thumbprint the install record keeps
(T888), and the one tagged hosts line out, then a resolver flush. A CA left in the machine
store is a CA this computer trusts forever, and the install folder it came from is gone
with its key. On Linux (the Steam Deck) there is no machine store to touch — the CA is
trusted inside the game's own Wine prefix, which is deleted with the server — so only the
hosts line goes, through `sudo -n`; when that cannot run, the one command comes back for
the player to run (Details).
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, runner, x509_names
from yulon.log import get_logger

logger = get_logger(__name__)

HOSTS_TAG = "# yulon-wow-forever"
"""The comment on the hosts line this module adds, so it can be found and taken out again."""

_PEM = re.compile(r"-----BEGIN CERTIFICATE-----\s*(.+?)\s*-----END CERTIFICATE-----", re.DOTALL)


class TrustError(RuntimeError):
    """The computer could not be made to trust the server; the message says what to do."""


UNCHECKED = (
    "This server's certificate could not be checked, so Yu'lon did not trust it. "
    "Details says why."
)
"""What a player reads when the CA does not pass `x509_names`: no commands, no prompt."""


def _pem(der: bytes) -> bytes:
    """`der` as a PEM certificate, in the one spelling `certutil` and `trust_ca` read."""
    body = base64.encodebytes(der).decode("ascii")
    return f"-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n".encode("ascii")


def _certificate_der(ca: Path) -> bytes:
    """`ca`'s one certificate's DER. Raises `TrustError(UNCHECKED)` when it holds none."""
    try:
        text = ca.read_text(encoding="ascii", errors="replace")
    except OSError as exc:
        logger.warning(f"{ca} could not be read: {exc}")
        raise TrustError(UNCHECKED) from exc
    found = _PEM.search(text)
    if found is None:
        logger.warning(f"{ca} holds no certificate")
        raise TrustError(UNCHECKED)
    try:
        return base64.b64decode("".join(found.group(1).split()), validate=True)
    except ValueError as exc:
        logger.warning(f"{ca} holds no readable certificate: {exc}")
        raise TrustError(UNCHECKED) from exc


def certificate_problem(ca: Path, portal: str) -> str | None:
    """Why `ca` must not be trusted for `portal`, or None when it may. Never raises.

    Without the copy `verified_ca()` makes: for a caller deciding whether the files must be
    made again (`forever_play`), where a refused certificate is the answer, not an error.
    """
    try:
        der = _certificate_der(ca)
    except TrustError:
        return f"{ca} could not be read as a certificate"
    return x509_names.problem(der, portal)


def verified_ca(ca: Path, portal: str) -> Path:
    """`ca` copied to a private temp file and checked; the copy's path. The caller deletes it.

    THE file the trust step then uses -- for its thumbprint and for `certutil -addstore` --
    because `ca.pem` sits in a folder the player (and every other account) can write to:
    it can be swapped between the check and the elevated command, and the elevated command
    runs seconds later, behind a prompt the player may leave open. The temp is created
    `mkstemp`'s own, 0600, and holds the bytes that were checked, so what is verified is
    what is installed.

    Raises `TrustError(UNCHECKED)` when the file is not this server's own CA: the reason
    (`x509_names.problem()`) is logged, which is what Details shows.
    """
    der = _certificate_der(ca)
    why = x509_names.problem(der, portal)
    if why is not None:
        logger.warning(f"refused {ca} as this computer's trust for {portal}: {why}")
        raise TrustError(UNCHECKED)
    handle, name = tempfile.mkstemp(prefix="yulon-ca-", suffix=".pem")
    verified = Path(name)
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(_pem(der))
    except BaseException:
        verified.unlink(missing_ok=True)
        raise
    return verified


def hosts_path() -> Path:
    """Windows' hosts file."""
    return (
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
    )


def thumbprint(ca: Path) -> str:
    """The CA certificate's SHA-1 thumbprint, as Windows' certificate stores name it."""
    try:
        text = ca.read_text(encoding="ascii", errors="replace")
    except OSError as exc:
        raise TrustError(
            f"The server's certificate {ca} could not be read ({exc.strerror or exc}). Start "
            "the server once: its first start makes it."
        ) from exc
    found = _PEM.search(text)
    if found is None:
        raise TrustError(f"{ca} holds no certificate. Start the server once: it makes it.")
    der = base64.b64decode("".join(found.group(1).split()))
    return hashlib.sha1(der).hexdigest().upper()  # noqa: S324 - Windows names certificates by it


def hosts_maps(text: str, portal: str) -> bool:
    """Whether hosts `text` sends `portal` to 127.0.0.1 (a commented-out line does not count)."""
    pattern = rf"^[ \t]*127\.0\.0\.1[ \t]+(?:\S+[ \t]+)*{re.escape(portal)}(?:[ \t]|$)"
    return re.search(pattern, text, re.MULTILINE | re.IGNORECASE) is not None


@dataclass(frozen=True)
class TrustSeams:
    """Everything this module reaches outside itself through. Real by default."""

    run: Callable[..., subprocess.CompletedProcess[str]] = runner.run
    batch: Callable[[list[list[str]]], tuple[list[str], Path] | None] = (
        platform.elevated_admin_batch
    )
    hosts: Callable[[], Path] = hosts_path
    which: Callable[[str], str | None] = shutil.which
    os_name: Callable[[], str] = platform.detect


def missing_commands(ca: Path, portal: str, *, seams: TrustSeams | None = None) -> list[list[str]]:
    """The administrator commands still needed for `ca` and `portal`; [] when all is in place."""
    s = seams if seams is not None else TrustSeams()
    commands: list[list[str]] = []
    thumb = thumbprint(ca)
    if s.run(["certutil", "-store", "Root", thumb]).returncode != 0:
        commands.append(["certutil", "-addstore", "-f", "Root", str(ca)])
    hosts = s.hosts()
    try:
        text = hosts.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if not hosts_maps(text, portal):
        commands.append(_append_hosts(hosts, portal))
    if commands:
        commands.append(["cmd", "/c", "ipconfig", "/flushdns"])
    return commands


def ensure(
    ca: Path,
    portal: str,
    *,
    seams: TrustSeams | None = None,
    record: Callable[[str], None] | None = None,
    stale: str | None = None,
) -> bool:
    """Make this computer trust `ca` and map `portal`; True if an administrator prompt was shown.

    `record` is handed the thumbprint once the CA is trusted (or was already), so the
    install's record can take it out again later (`remove`). `stale` is the thumbprint of
    the CA this install trusted before (see `_trust`). Raises `TrustError` when the player
    says No or a command fails, with what to do.
    """
    s = seams if seams is not None else TrustSeams()
    verified = verified_ca(ca, portal)
    try:
        return _trust(verified, portal, s, record, stale)
    finally:
        try:
            verified.unlink()
        except OSError as exc:
            logger.warning(f"could not remove the checked certificate copy {verified}: {exc}")


def _trust(
    ca: Path, portal: str, s: TrustSeams, record: Callable[[str], None] | None, stale: str | None
) -> bool:
    """`ensure()` over the checked copy `ca`: what is missing, behind one prompt.

    `stale` is the thumbprint of a CA this install trusted before (the one Play replaced):
    its `certutil -delstore` goes first, in the same batch, so the old root leaves the
    store in the same prompt that adds the new one.
    """
    commands = missing_commands(ca, portal, seams=s)
    if stale is not None:
        try:
            installed = s.run(["certutil", "-store", "Root", stale]).returncode == 0
        except OSError:
            installed = False
        if installed:
            commands.insert(0, ["certutil", "-delstore", "Root", stale])
            if ["cmd", "/c", "ipconfig", "/flushdns"] not in commands:
                commands.append(["cmd", "/c", "ipconfig", "/flushdns"])
    thumb = thumbprint(ca)
    if record is not None:
        try:
            record(thumb)
        except OSError as exc:
            logger.warning(f"could not record the trusted certificate's thumbprint: {exc}")
    if not commands:
        return False
    wrapped = s.batch(commands)
    if wrapped is None:  # already an administrator (or not Windows): run them as they are
        failed = [c for c in commands if s.run(c).returncode != 0]
        _check(ca, portal, s, failed)
        return False
    argv, results_path = wrapped
    try:
        try:
            proc = s.run(argv)
        except OSError as exc:
            proc = subprocess.CompletedProcess(argv, 1, "", str(exc))
        if proc.returncode == platform.UAC_DECLINED:
            raise TrustError(DECLINED)
        results = platform.read_batch_results(results_path, len(commands))
    finally:
        try:
            results_path.unlink()
        except OSError:
            pass
    if proc.returncode != 0 or results is None:
        raise TrustError(_by_hand(commands))
    failed = [c for c, (rc, _out) in zip(commands, results, strict=True) if rc != 0]
    _check(ca, portal, s, failed)
    logger.info(f"trusted {ca} and mapped {portal} to 127.0.0.1")
    return True


DECLINED = (
    "You said No to the Windows administrator prompt, so this computer does not trust the "
    "WoW Forever server yet and the game was not started. Press Play again and choose Yes; "
    "it is asked once."
)


def _check(ca: Path, portal: str, s: TrustSeams, failed: Sequence[list[str]]) -> None:
    still = missing_commands(ca, portal, seams=s)
    if still:
        raise TrustError(_by_hand(still))
    for command in failed:
        logger.warning(f"{platform.command_text(command)} failed, but what it does is in place")


def _by_hand(commands: Sequence[list[str]]) -> str:
    return (
        "This computer could not be made to trust the WoW Forever server. Run these in an "
        "administrator terminal, then press Play again:\n"
        + "\n".join(platform.command_text(c) for c in commands)
    )


def hosts_line(portal: str) -> str:
    """The one line this module adds to hosts: loopback, this server's name, and our tag."""
    return f"127.0.0.1 {portal} {HOSTS_TAG}"


def tagged(hosts: Path) -> bool:
    """Whether `hosts` holds this module's line (read unelevated; a missing file has none)."""
    try:
        return HOSTS_TAG in hosts.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _append_hosts(hosts: Path, portal: str) -> list[str]:
    """`cmd`'s one-line append. An empty echo first: a hosts file with no final newline
    must not have this line glued to its last one, and the path is quoted, so a
    `SystemRoot` with a space in it stays one file name (T888)."""
    return ["cmd", "/c", f'(echo.& echo {hosts_line(portal)})>>"{hosts}"']


def _strip_hosts(hosts: Path) -> list[str]:
    """`cmd`'s one-line removal of the tagged line, from an otherwise-empty file too.

    `findstr /v` exits 1 when it finds no line to keep, and a plain `&&` would then skip
    the `move` and leave the old hosts file in place (T888). The `|| type nul > tmp` writes
    an empty file instead, so the move always lands. Same folder, so the move replaces.
    """
    kept = hosts.with_name(hosts.name + ".yulon")
    return [
        "cmd",
        "/c",
        f'(findstr /v /c:"{HOSTS_TAG}" "{hosts}" > "{kept}" || type nul > "{kept}") && '
        f'move /y "{kept}" "{hosts}"',
    ]


def remove_commands(
    ca: Path | None = None, *, thumb: str | None = None, hosts: Path | None = None
) -> list[list[str]]:
    """What undoes `ensure()`: the CA out of Root, and this module's hosts line out of hosts.

    `thumb` is the thumbprint the install record kept (`state.KnownInstall.trusted_ca_thumbprint`),
    which is what makes this work after the certs folder — and the CA the thumbprint is of —
    is gone. `ca` is the fallback for an install that predates that record. The hosts half
    is one command and one file, whatever the CA's answer was.
    """
    known = thumb if thumb is not None else (thumbprint(ca) if ca is not None else None)
    commands: list[list[str]] = []
    if known:
        commands.append(["certutil", "-delstore", "Root", known])
    commands.append(_strip_hosts(hosts if hosts is not None else hosts_path()))
    return commands


def remove(
    *, ca: Path | None = None, thumb: str | None = None, seams: TrustSeams | None = None
) -> tuple[str, ...]:
    """Take this install's trust off the computer; the commands it could not run.

    Windows: the CA's thumbprint out of `LocalMachine\\Root` and the tagged hosts line out,
    in ONE elevated batch (one prompt) — plus a resolver flush, because Windows caches the
    name it no longer has to redirect. Linux: there is no machine store this app put
    anything in (the CA lives in the game's own prefix), so only the hosts line, through
    `sudo -n`. What cannot run there comes back as the command for the player, one line
    each, which the caller shows under Details.
    """
    s = seams if seams is not None else TrustSeams()
    hosts = s.hosts()
    if s.os_name() != "windows":
        if not tagged(hosts):
            return ()
        command = ["sudo", "-n", "sed", "-i", f"/{HOSTS_TAG}/d", str(hosts)]
        if s.which("sudo") is not None:
            try:
                if s.run(command).returncode == 0:
                    logger.info("took WoW Forever's hosts line out")
                    return ()
            except OSError as exc:
                logger.warning(f"sudo could not take the hosts line out: {exc}")
        return (platform.command_text(command),)
    commands = [
        *remove_commands(ca, thumb=thumb, hosts=hosts),
        ["cmd", "/c", "ipconfig", "/flushdns"],
    ]
    wrapped = s.batch(commands)
    if wrapped is None:
        failed = [c for c in commands if _ran(s, c) != 0]
    else:
        argv, results_path = wrapped
        try:
            try:
                proc = s.run(argv)
            except OSError as exc:
                proc = subprocess.CompletedProcess(argv, 1, "", str(exc))
            results = platform.read_batch_results(results_path, len(commands))
        finally:
            try:
                results_path.unlink()
            except OSError:
                pass
        if proc.returncode == platform.UAC_DECLINED:
            return tuple(platform.command_text(c) for c in commands)
        if results is None:
            return tuple(platform.command_text(c) for c in commands)
        failed = [c for c, (rc, _out) in zip(commands, results, strict=True) if rc != 0]
    if failed:
        logger.warning(
            "WoW Forever's trust could not be taken off this computer: "
            + "; ".join(platform.command_text(c) for c in failed)
        )
        return tuple(platform.command_text(c) for c in failed)
    logger.info("took WoW Forever's CA and hosts line off this computer")
    return ()


def _ran(s: TrustSeams, command: list[str]) -> int:
    """`command`'s exit status, 1 when it could not run at all."""
    try:
        return s.run(command).returncode
    except OSError as exc:
        logger.warning(f"{platform.command_text(command)} could not run: {exc}")
        return 1
