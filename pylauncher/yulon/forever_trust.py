"""WoW Forever's login trust: this computer trusts the install's CA, and hosts names its server.

The 70707 client dials the Battle.net name in its config's portal over TLS and checks the
certificate against the computer's trusted roots. A WoW Forever install makes its own CA
on first start (/opt/forever/yulon/certs.sh in the server image), name-constrained to
`actual.battle.net`, so trusting it lets it vouch for those names and no others, and its
key never leaves the install's certs/ folder. Play therefore needs, once per computer:

* the CA in `LocalMachine\\Root` (`certutil -addstore Root`), and
* `127.0.0.1 <portal>` in the hosts file, so the name reaches this computer's server.

Both need an administrator, so they go behind ONE UAC prompt
(`platform.elevated_admin_batch()`), and only what is missing is done. Reading is
unelevated and has no side effects. `remove_commands()` is what an uninstall would run.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, runner
from yulon.log import get_logger

logger = get_logger(__name__)

HOSTS_TAG = "# yulon-wow-forever"
"""The comment on the hosts line this module adds, so it can be found and taken out again."""

_PEM = re.compile(r"-----BEGIN CERTIFICATE-----\s*(.+?)\s*-----END CERTIFICATE-----", re.DOTALL)


class TrustError(RuntimeError):
    """The computer could not be made to trust the server; the message says what to do."""


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

    run: Callable[[list[str]], subprocess.CompletedProcess[str]] = runner.run
    batch: Callable[[list[list[str]]], tuple[list[str], Path] | None] = (
        platform.elevated_admin_batch
    )
    hosts: Callable[[], Path] = hosts_path


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
        # An empty echo first: a hosts file that does not end in a newline must not have
        # this line glued to its last one.
        commands.append(["cmd", "/c", f"(echo.& echo 127.0.0.1 {portal} {HOSTS_TAG})>>{hosts}"])
    if commands:
        commands.append(["cmd", "/c", "ipconfig", "/flushdns"])
    return commands


def ensure(ca: Path, portal: str, *, seams: TrustSeams | None = None) -> bool:
    """Make this computer trust `ca` and map `portal`; True if an administrator prompt was shown.

    Raises `TrustError` when the player says No or a command fails, with what to do.
    """
    s = seams if seams is not None else TrustSeams()
    commands = missing_commands(ca, portal, seams=s)
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


def remove_commands(ca: Path) -> list[list[str]]:
    """What undoes `ensure()`: the CA out of Root, and this module's hosts line out of hosts."""
    hosts = hosts_path()
    return [
        ["certutil", "-delstore", "Root", thumbprint(ca)],
        [
            "cmd",
            "/c",
            f'findstr /v /c:"{HOSTS_TAG}" "{hosts}" > "{hosts}.yulon" && move /y "{hosts}.yulon" '
            f'"{hosts}"',
        ],
    ]
