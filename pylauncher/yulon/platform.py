"""OS detection + per-OS config dir + silent Docker/WSL2 provisioning stubs.

Platform-specific "ensure a Linux container environment exists" logic lives
here while keeping the rest of the app 100% shared. See pyplan/README.md §3
(the kernel constraint) and §11 (config dir locations).
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Literal, Protocol

from yulon import runner
from yulon.log import get_logger

logger = get_logger(__name__)

# The app/data directory name, lowercase everywhere (style-guide §6a; matches
# the `yulon` package, `yulon.log`, and the PyInstaller binary name).
APP_DIR_NAME = "yulon"

# A normalized platform id.
PlatformId = Literal["linux", "windows", "macos"]


def detect() -> PlatformId:
    """Return a normalized platform identifier.

    Collapses `sys.platform` down to one of `linux` / `windows` / `macos`, the
    only granularity the rest of the app cares about (README §3's three
    provisioning paths).

    `win32` and `cygwin` both map to `windows` (Cygwin Python still runs on a
    real Windows machine and needs the Windows config-dir/provisioning path,
    not the Linux one). Anything else that isn't `darwin` — real Linux
    (including WSL, where `sys.platform` is also `"linux"`), and any BSD/other
    POSIX variant not explicitly supported yet — is treated as `linux` as a
    best-effort default, and logged so an unrecognized platform doesn't fail
    silently.
    """
    current = sys.platform
    if current in ("win32", "cygwin"):
        return "windows"
    if current == "darwin":
        return "macos"
    if current not in ("linux", "linux2"):
        logger.warning(f"Unrecognized sys.platform={current!r}; treating as linux")
    return "linux"


def config_dir() -> Path:
    """Return the per-OS directory for app state (README §11).

    This is app state (remembered server paths, cached manifests, the log file),
    **not** server data — server files stay wherever the user chose at install
    time. Uses the platform's conventional per-user data directory, each with a
    documented fallback if the expected environment variable is unset:

    - Linux:   `~/.local/share/yulon/` — honors `XDG_DATA_HOME` if set
      (non-empty), else falls back to `~/.local/share`.
    - Windows: `%APPDATA%\\yulon\\` — honors `APPDATA` if set, else falls back
      to `~/AppData/Roaming` (the common default; not guaranteed correct for
      every profile/folder-redirection configuration, but `APPDATA` is set in
      essentially every real Windows user session).
    - macOS:   `~/Library/Application Support/yulon/` — no environment
      override; this is the fixed Apple convention for a non-sandboxed app.
    """
    platform = detect()
    logger.debug(f"config_dir() resolving for platform={platform!r}")

    if platform == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / APP_DIR_NAME

    if platform == "macos":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME


# ---------------------------------------------------------------- networking
# README §13 / roadmap 3.4: the per-OS firewall commands, LAN/public IP
# detection and the WSL2 port proxy live HERE, once, as shared behavior; the
# port numbers come from catalog.json (data), never from this module.

FirewallBackend = Literal["ufw", "firewalld", "netsh", "alf", "none"]
"""Which firewall tool a host uses. `alf` is macOS's Application Firewall.

A closed Literal on purpose: adding a member makes mypy point at every site
that does not handle it, which is how a Mac stopped silently falling into
`none` and being told about ufw and firewalld.
"""

_PUBLIC_IP_SERVICES: tuple[str, ...] = ("https://icanhazip.com", "https://api.ipify.org")
_CGNAT = IPv4Network("100.64.0.0/10")
_PRIVATE = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


def in_wsl() -> bool:
    """True when running inside WSL (Linux kernel built by Microsoft)."""
    try:
        with open("/proc/version", encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def is_steamos() -> bool:
    """True on SteamOS (whose root filesystem is read-only until unlocked)."""
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            return any(line.strip() == "ID=steamos" for line in fh)
    except OSError:
        return False


def detect_firewall(which: Callable[[str], str | None] | None = None) -> FirewallBackend:
    """Which firewall tool this host uses: netsh (Windows), alf (macOS), ufw, firewalld, or none.

    `alf` is in that list because leaving it out of this sentence is the same
    mistake as leaving it out of the code: answering "none" for a Mac is what
    told a Mac user about ufw. A one-line summary that enumerates the answers
    has to enumerate all of them.
    """
    if detect() == "windows":
        return "netsh"
    if detect() == "macos":
        # It ships with macOS, so the question is never "is a tool present"
        # but "what is it set to" — which is `detect_alf_state()`'s job,
        # not this one's. Answering "none" here is what told a Mac user about
        # ufw.
        return "alf"
    find = which if which is not None else _which
    if find("ufw"):
        return "ufw"
    if find("firewall-cmd"):
        return "firewalld"
    return "none"


# ------------------------------------------------------- the macOS firewall
# macOS's firewall is a different KIND of thing, and the difference decides the
# whole design: the Application Firewall is per-APPLICATION, not per-port. Its
# configuration profile schema has no port or protocol key at all, so "open TCP
# 3724" cannot be expressed. The layer that could express it is `pf`, which
# Apple documents as not-API (TN3165) and which no third-party app should be
# editing. And every mutation of either needs root, which the macOS install
# path deliberately does not have.
#
# Then the part that settles it: the binary the firewall evaluates for a
# published container port is **Docker Desktop's**, not ours. The server
# listens inside Docker's Linux VM; what accepts a LAN player's connection on
# the host is `com.docker.backend`. Allow-listing Yu'lon would be theatre.
#
# So `firewall_commands("alf", ...)` returning `[]` is the correct answer, not
# a gap, and what macOS needs instead is an honest read of the state. Every
# claim in this section is inherited from documentation rather than measured —
# nobody on this project has a Mac — and `pyplan/checklist.md` carries the list
# of checks that settle each one.

_SOCKETFILTERFW = Path("/usr/libexec/ApplicationFirewall/socketfilterfw")
"""The Application Firewall's CLI. A fixed system path, never on PATH."""

_DOCKER_BACKEND = Path("/Applications/Docker.app/Contents/MacOS/com.docker.backend")
"""The binary that actually receives a player's connection on a Mac.

Docker Desktop's host-side listener: the container publishes into the VM, and
this process holds the socket on the host. Which is why the firewall question
for a Mac is "is Docker allowed", never "is Yu'lon allowed". The path is a
claim to check — see the macOS checks in `pyplan/checklist.md`.
"""

AlfAppStatus = Literal["allowed", "blocked", "unlisted"]


@dataclass(frozen=True)
class AlfState:
    """What the macOS Application Firewall reports. `None` always means "unreadable".

    Three independent reads, so one failing does not poison the others, and
    `None` is never rounded to either neighbour — the same tri-state rule
    `preflight.py` holds itself to. Apple exposes no supported API for
    observing this state; the `socketfilterfw` getters are the only channel,
    and each can fail on its own.
    """

    enabled: bool | None = None
    block_all: bool | None = None
    docker_backend: AlfAppStatus | None = None

    def describe(self) -> str:
        """One status line for the Networking tab. Lives here: it is a platform fact.

        Not in `ui/` (no business logic in a view) and not in `networking.py`
        (which names no OS-specific tooling). Every unread field says so out
        loud rather than reading as a pass.
        """
        if self.enabled is None:
            return (
                "macOS firewall: unchecked — could not read the firewall state. "
                "Unchecked is not a pass."
            )
        if not self.enabled:
            return "macOS firewall: off — nothing is being blocked, no rule is needed."
        if self.block_all:
            return (
                "macOS firewall: on and set to block ALL incoming connections — the per-app "
                "allow list is ignored."
            )
        unread = "" if self.block_all is not None else ' (could not read the "block all" setting)'
        if self.docker_backend == "allowed":
            return (
                "macOS firewall: on — Docker Desktop (com.docker.backend) is allowed to receive "
                "incoming connections." + unread
            )
        if self.docker_backend == "blocked":
            return (
                "macOS firewall: on — Docker Desktop (com.docker.backend) is BLOCKED from "
                "receiving incoming connections." + unread
            )
        if self.docker_backend == "unlisted":
            return (
                "macOS firewall: on — Docker Desktop is not in the allow list yet; macOS will "
                "decide the first time the server listens." + unread
            )
        return (
            "macOS firewall: on — could not read whether Docker Desktop is allowed "
            "(unchecked — not a pass)." + unread
        )


def _alf_read(do: RunCmd, *args: str) -> str | None:
    """One `socketfilterfw` getter's stdout, lowercased, or None if it could not be read.

    The getters need no privilege and never prompt, which is what makes probing
    safe on a path that has no sudo. Anything unexpected — a non-zero exit, an
    `OSError`, a binary that is not there — is `None` rather than a guess.
    """
    try:
        proc = do([str(_SOCKETFILTERFW), *args])
    except OSError as exc:
        logger.info(f"could not run socketfilterfw {' '.join(args)}: {exc}")
        return None
    if proc.returncode != 0:
        logger.info(f"socketfilterfw {' '.join(args)} exited {proc.returncode}")
        return None
    return proc.stdout.strip().lower()


def _alf_flag(said: str | None, *, yes: tuple[str, ...], no: tuple[str, ...]) -> bool | None:
    """Three answers from one getter: True, False, or "that is not a reading".

    The negative patterns are tested FIRST, because macOS's wordings nest —
    "disabled" is a substring of nothing here, but "blocked" is a substring of
    "not blocked" one function down, and the same class of trap is one wording
    change away in either of these. Anything matching neither is `None`.
    """
    if said is None:
        return None
    if any(pattern in said for pattern in no):
        return False
    if any(pattern in said for pattern in yes):
        return True
    logger.info(f"socketfilterfw said something unrecognised: {said!r}")
    return None


def detect_alf_state(run: RunCmd | None = None, docker_backend: Path | None = None) -> AlfState:
    """Read the macOS firewall, unprivileged and without prompting for anything.

    Read-only on purpose. Every setter needs root; this path has no sudo, and a
    GUI-launched process has no cached sudo timestamp, so `sudo -n` would fail
    every single time and a password prompt is forbidden here. Where a root
    action genuinely helps, `alf_unblock_commands()` produces the command for
    the user to run.

    Off macOS — and on a Mac with no `socketfilterfw` — this answers all-`None`
    without spawning anything, which is also what makes it safe to call from
    the test box.
    """
    # Bounded, like every other probe in this module. `runner.run()`'s own
    # docstring says the callers that need a timeout are the ones running on a
    # GUI thread, and this is a public function anyone can call synchronously —
    # today's only caller happens to be on a worker thread, which is the
    # caller's property, not this function's (review, 2026-08-24).
    do: RunCmd = run if run is not None else (lambda argv: runner.run(argv, timeout=5.0))
    app = docker_backend if docker_backend is not None else _DOCKER_BACKEND
    if not _SOCKETFILTERFW.is_file():
        return AlfState()

    # Each field is matched three ways, not two. "The command succeeded" is not
    # the same as "the output was recognised", and collapsing them is how a
    # firewall that is ON reads as OFF: `enabled` used to be
    # `"state = 1" in said or "state = 2" in said`, so ANY unrecognised wording
    # — a future macOS phrasing, an unanticipated locale — answered False, and
    # `describe()` then said "off, nothing is being blocked, no rule is needed"
    # about a machine that may be blocking every player. That is the worst
    # outcome this design has, and it contradicted `AlfState`'s own docstring
    # two lines above it (review of this commit, 2026-08-24).
    enabled = _alf_flag(
        _alf_read(do, "--getglobalstate"), yes=("state = 1", "state = 2"), no=("state = 0",)
    )
    blocked_all = _alf_flag(
        _alf_read(do, "--getblockall"),
        yes=("enabled", "block all non-essential"),
        no=("disabled",),
    )

    status: AlfAppStatus | None = None
    app_said = _alf_read(do, "--getappblocked", str(app))
    if app_said is not None:
        # Order matters: "not blocked" contains "blocked", so the negative
        # readings are tested first or every allowed app reads as blocked.
        if "not part of the firewall" in app_said:
            status = "unlisted"
        elif "not blocked" in app_said or "permitted" in app_said:
            status = "allowed"
        elif "blocked" in app_said:
            status = "blocked"
    return AlfState(enabled=enabled, block_all=blocked_all, docker_backend=status)


def alf_unblock_commands(app: Path | None = None) -> list[list[str]]:
    """The root-only argv that lets `app` through the macOS firewall — to SHOW, not to run.

    `networking.apply()` never executes this. Mutating the Application Firewall
    needs a password this path will not ask for, and `socketfilterfw`'s writes
    have their own history of being ignored under management policy — so even a
    zero exit could not honestly be reported as done. The user runs it.
    """
    return [[str(_SOCKETFILTERFW), "--unblockapp", str(app or _DOCKER_BACKEND)]]


@dataclass(frozen=True)
class ElevationPolicy:
    """How `networking.apply()` elevates one backend's commands, and what to say if it cannot."""

    prefix: tuple[str, ...] = ()
    retry_hint: str = ""


def elevation_policy(backend: FirewallBackend) -> ElevationPolicy:
    """Per-backend elevation, so a new backend cannot inherit the wrong advice.

    This replaced a two-branch `linux` boolean whose else-branch told everyone
    who was not on ufw/firewalld to "run it in an Administrator PowerShell" —
    which a Mac user would have been told the moment `alf` existed. `alf` and
    `none` emit no commands at all, so an empty hint is the honest one.
    """
    if backend in ("ufw", "firewalld"):
        return ElevationPolicy(("sudo", "-n"), " with sudo")
    if backend in ("netsh", "none"):
        # `none` belongs HERE, not with `alf`. It emits no firewall commands,
        # but it is the backend a WSL2 distro with no ufw/firewall-cmd gets —
        # and the loopback path still queues `netsh` portproxy commands for it.
        # Grouping it with `alf` dropped the privilege hint from exactly those,
        # which the old `linux`-boolean got right by accident (review of this
        # commit, 2026-08-24).
        return ElevationPolicy((), " in an Administrator PowerShell")
    return ElevationPolicy()


def _which(name: str, path: str | None = None) -> str | None:
    """`shutil.which`, as one seam.

    `path` searches a search-path this process is not running with, which is the
    whole of `docker_programs()`'s Windows case. `None` means "the PATH we
    started with", exactly as `shutil.which` already defines it, so every
    existing caller keeps its shape and its `Callable[[str], str | None]` type.
    """
    return shutil.which(name, path=path)


def firewall_commands(
    backend: FirewallBackend, ports: Iterable[int], *, rule_prefix: str, steamos: bool = False
) -> list[list[str]]:
    """The exact commands that open `ports` (TCP, inbound) on `backend`, as argv lists.

    Mirrors the guide's per-OS blocks. Linux commands are returned WITHOUT
    `sudo`; the caller decides how to elevate (and what to show the user if it
    cannot). On SteamOS the read-only root is unlocked around installing ufw
    and relocked after, exactly like the guide.
    """
    ports = list(ports)
    if backend == "alf":
        # No port vocabulary exists in the macOS Application Firewall, the
        # binary it evaluates is Docker Desktop's rather than ours, and every
        # mutation needs a root this path will not ask for. `[]` is the
        # honest answer; `networking.plan()` reports the firewall's STATE
        # instead.
        return []
    if backend == "netsh":
        return [
            [
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule_prefix} {port}",
                "protocol=TCP",
                "dir=in",
                f"localport={port}",
                "action=allow",
            ]
            for port in ports
        ]
    if backend == "ufw":
        cmds: list[list[str]] = []
        if steamos:
            cmds += [["steamos-readonly", "disable"], ["pacman", "-Sy", "--noconfirm", "ufw"]]
        cmds += [["ufw", "allow", f"{port}/tcp"] for port in ports]
        cmds += [["ufw", "--force", "enable"]]
        if steamos:
            cmds += [["systemctl", "enable", "ufw"], ["steamos-readonly", "enable"]]
        return cmds
    if backend == "firewalld":
        return (
            [["systemctl", "enable", "--now", "firewalld"]]
            + [["firewall-cmd", "--permanent", f"--add-port={port}/tcp"] for port in ports]
            + [["firewall-cmd", "--reload"]]
        )
    return []


def portproxy_commands(listen_address: str, ports: Iterable[int]) -> list[list[str]]:
    """WSL2 `netsh interface portproxy` rules forwarding `listen_address:port` → 127.0.0.1:port."""
    return [
        [
            "netsh",
            "interface",
            "portproxy",
            "add",
            "v4tov4",
            f"listenaddress={listen_address}",
            f"listenport={port}",
            "connectaddress=127.0.0.1",
            f"connectport={port}",
        ]
        for port in ports
    ]


def detect_lan_ip() -> str | None:
    """The host's LAN IPv4 — the address other players on the same network use.

    Uses the routing table (a connected UDP socket to a public address; no
    packet is sent). Inside WSL that answers with the 172.x guest address,
    which the guide says never to use, so WSL asks the Windows side instead.
    """
    if in_wsl():
        return _windows_lan_ip_from_wsl()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 53))
            ip = str(sock.getsockname()[0])
    except OSError as exc:
        logger.debug(f"detect_lan_ip() failed: {exc}")
        return None
    return None if ip.startswith("127.") else ip


def _windows_lan_ip_from_wsl() -> str | None:
    """Ask Windows (via powershell.exe) for the IPv4 of the adapter with a default gateway."""
    proc = runner.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "(Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway -ne $null} "
            "| Select-Object -First 1).IPv4Address.IPAddress",
        ]
    )
    ip = proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else ""
    return ip or None


@dataclass(frozen=True)
class PublicIpResult:
    """The public-IP probe's answer, plus whether it was TLS and not the network that failed.

    This used to be a bare `str | None`, and `networking.plan()` renders a None
    as "could not determine the public IP (offline?)". That is the one diagnosis
    this probe must never guess at: on the fresh Windows 11 box the downloads
    block below was written for, OpenSSL could not build a certificate chain
    while the machine was perfectly online, so the report sent the user to look
    at their router when the fix was Windows Update. The flag is what lets the
    report tell those two apart.
    """

    address: str | None
    verification_failed: bool = False


def detect_public_ip(
    http_get: Callable[[str], str] | None = None, services: Iterable[str] = _PUBLIC_IP_SERVICES
) -> PublicIpResult:
    """The public IPv4 as seen from the internet (icanhazip/ipify), and why it failed.

    `verification_failed` is set when at least one service was reached and its
    certificate could not be verified, and none of them answered — i.e. the
    probe found a server and refused to trust it, which is a machine problem
    with a different fix than having no route out at all.
    """
    get = http_get if http_get is not None else _http_get_text
    verification_failed = False
    for url in services:
        try:
            text = get(url).strip()
            IPv4Address(text)
            return PublicIpResult(text)
        except (OSError, ValueError) as exc:
            verification_failed = verification_failed or (
                isinstance(exc, OSError) and _is_verification_failure(exc)
            )
            logger.debug(f"detect_public_ip() via {url} failed: {exc}")
    return PublicIpResult(None, verification_failed)


def _http_get_text(url: str) -> str:
    """A small GET as text, over the same verified TLS the installer download uses.

    The context is not optional here even though nothing executable is fetched:
    without it this call inherits OpenSSL's snapshot of the Windows root store
    and fails on exactly the hosts `verify_context()` exists to cover — and
    `detect_public_ip()` would report that as "offline".
    """
    request = urllib.request.Request(url, headers={"User-Agent": "yulon"})
    with urllib.request.urlopen(request, timeout=5.0, context=verify_context()) as resp:
        return str(resp.read().decode("utf-8", errors="replace"))


def is_cgnat(public_ip: str) -> bool:
    """True if the 'public' address is carrier-grade NAT (100.64/10) or a private range.

    Either means the ISP does not give this connection a real public address,
    so router port forwarding cannot work (the guide's CGNAT warning).
    """
    addr = IPv4Address(public_ip)
    return addr in _CGNAT or any(addr in net for net in _PRIVATE)


@dataclass(frozen=True)
class PortProbe:
    """Result of trying to reach `host:port` over TCP from this machine."""

    host: str
    port: int
    status: Literal["open", "closed", "unknown"]
    detail: str


def probe_tcp(host: str, port: int, timeout: float = 3.0) -> PortProbe:
    """Try a TCP connect. `unknown` = refused/timeout from INSIDE the LAN, which most
    home routers do for their own public IP (no hairpin NAT) — not proof the
    forward is missing; the report says so instead of claiming failure."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return PortProbe(host, port, "open", "connection accepted")
    except (TimeoutError, ConnectionRefusedError, OSError) as exc:
        return PortProbe(host, port, "unknown", f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------- provisioning
# Roadmap 5.1 / README §3b: "the app installs everything" — Docker Engine on
# Linux (distro package manager; SteamOS unlocks/relocks the read-only root),
# Docker Desktop + WSL2 on Windows, Docker Desktop on macOS. Everything that
# reaches outside the process is a seam so the plans are unit-testable and a
# `dry_run` shows the user exactly what will happen. Nothing here pretends:
# every step that could not run is named in `ProvisionReport`, and a reboot
# or re-login the platform genuinely needs is reported, not hidden.

PackageManager = Literal["pacman", "apt", "dnf", "zypper"]

SINGLE_QUOTE = chr(39)
DOCKER_DESKTOP_WINDOWS_URL = (
    "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
)
DOCKER_DESKTOP_MAC_URLS: dict[str, str] = {
    "arm64": "https://desktop.docker.com/mac/main/arm64/Docker.dmg",
    "x86_64": "https://desktop.docker.com/mac/main/amd64/Docker.dmg",
}
_DOCKER_READY_TIMEOUT_SECONDS = 180.0
_DOCKER_READY_POLL_SECONDS = 3.0
_MANUAL_DOCKER_DESKTOP = (
    "Download and install Docker Desktop by hand: "
    "https://www.docker.com/products/docker-desktop/"
)
# The sentence that is true wherever a TLS check fails on a box like the one in
# the downloads block below: the root store, not the network, is what broke, and
# Windows Update is what fixes it. Shared (style-guide §4) so the installer's
# manual step and the networking report cannot drift into two different answers.
CERT_VERIFY_FIX = (
    "This machine could not verify the server's certificate, usually because it is missing a "
    "root certificate. On Windows, run Windows Update (it installs the current roots) and "
    "try again."
)
_MANUAL_ROOT_CERTS = f"{CERT_VERIFY_FIX} Yu'lon will not install software it could not verify."
_MANUAL_WSL = (
    "Open an Administrator PowerShell and run: wsl --install --no-distribution, then reboot."
)
_MANUAL_START_DOCKER_DESKTOP = (
    "Yu'lon could not find Docker Desktop on this PC. Open the Start menu, type "
    "\"Docker Desktop\", start it, and wait until it says 'Engine running' — then try again. "
    "If it is not in the Start menu it is not installed: get it from "
    "https://www.docker.com/products/docker-desktop/"
)


class ProvisionError(RuntimeError):
    """Provisioning hit something it cannot work around (message is user-readable)."""


DockerGroupOutcome = Literal[
    "granted", "join-failed", "declined", "not-asked", "already-member", "not-applicable"
]
"""What happened to the docker-group question on this run.

Six values rather than a bool because the five ways of *not* joining are not
the same event and must not read as one: the user said no, nobody was there to
ask, they were already a member, this platform has no such group at all — or
they said yes and the `usermod` that followed did not run. Only `granted` and
`already-member` mean the user can drive Docker afterwards, and only those two
may print the log-out-and-back-in line.

`join-failed` was the sixth, added late. The field used to carry the CONSENT
answer, so a yes whose `usermod` was refused for want of a sudo ticket was
recorded as `granted` — a support JSON saying the user is in the docker group
when they are not, which is precisely the "a way of not joining reads as
joining" failure the five values existed to prevent, reintroduced one layer up.
The manual steps had always drawn the distinction; only the machine-readable
field did not (review residual, 2026-08-24).

This field is the artifact roadmap 6.2's definition of done means by "the
preflight records explicit consent" — it rides `--provision`'s support JSON,
so what was asked, answered and then actually done is legible from a bug report.
"""


@dataclass(frozen=True)
class ProvisionReport:
    """What `ensure_docker()` / `ensure_wsl2()` did, and what is still needed."""

    platform: PlatformId
    done: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    manual_steps: tuple[str, ...] = ()
    reboot_required: bool = False
    docker_ready: bool = False
    docker_group: DockerGroupOutcome = "not-applicable"

    @property
    def ok(self) -> bool:
        """True when a daemon answers and nothing is left for the user to do first."""
        return self.docker_ready and not self.reboot_required


RunCmd = Callable[[list[str]], subprocess.CompletedProcess[str]]
Downloader = Callable[[str, Path], Path]


# ------------------------------------------------------ finding the docker CLI
# Windows hands a process its environment once, when it is created, and never
# revises it. Docker Desktop's installer adds its own `resources\bin` to the
# PATH held in the REGISTRY — so the launcher that just ran that installer is
# the one process on the machine guaranteed not to see it. These two keys are
# where that PATH actually lives.
_MACHINE_ENVIRONMENT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_USER_ENVIRONMENT_KEY = "Environment"


def _registry_search_path() -> str:
    """The machine + user PATH as they stand on disk right now (Windows only).

    Both are read, in that order, because the user half is not optional:
    measured on Windows 11 Pro 26200 (2026-08-23), Docker Desktop had installed
    itself to `%LOCALAPPDATA%\\Programs\\DockerDesktop\\resources\\bin` and written
    that directory to the **user** PATH, while the machine PATH named no docker
    directory at all. A fix that read only
    `HKLM\\...\\Session Manager\\Environment` — the key everyone reaches for —
    would have found nothing on the very machine the defect was reproduced on.

    The values are `REG_EXPAND_SZ` (type 2), which means the stored string keeps
    `%USERPROFILE%` and friends literal; the same box had four such entries
    (`%USERPROFILE%\\AppData\\Local\\Microsoft\\WindowsApps`,
    `%USERPROFILE%\\.dotnet\\tools`, `%NVM_HOME%`, `%NVM_SYMLINK%`). Handing those
    to `which` unexpanded searches directories that do not exist, so they are
    expanded here — against this process's environment, which is what Windows
    does with them too. An unset variable stays literal and simply matches no
    directory, which is harmless.

    `winreg` is imported dynamically for the reason `runner.open_pty` fetches
    `os.openpty` dynamically: the module does not exist off Windows, and mypy
    type-checks this file for those platforms too.

    Raises:
        ImportError: not running on Windows (no `winreg`). Callers guard on
            `detect()`; this is the belt to that braces.
    """
    winreg = importlib.import_module("winreg")
    parts: list[str] = []
    for hive, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, _MACHINE_ENVIRONMENT_KEY),
        (winreg.HKEY_CURRENT_USER, _USER_ENVIRONMENT_KEY),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value = winreg.QueryValueEx(key, "Path")[0]
        except OSError as exc:
            # A user with no `Path` value of their own is normal, not a fault.
            logger.debug(f"no readable PATH under {subkey}: {exc}")
            continue
        if isinstance(value, str) and value:
            parts.append(value)
    return os.path.expandvars(os.pathsep.join(parts))


def _windows_docker_bins() -> tuple[Path, ...]:
    """The `resources\\bin` layouts Docker Desktop is known to use, best first.

    A last resort, for a box whose registry cannot be read at all. The first
    entry is the one actually observed (see `_registry_search_path()`); the
    second is the historical per-machine layout that every "add Docker to your
    PATH" answer still names, and which was measured ABSENT on that same box —
    so it is a guess kept for older installs, not evidence.
    """
    roots: tuple[tuple[str | None, tuple[str, ...]], ...] = (
        (os.environ.get("LOCALAPPDATA"), ("Programs", "DockerDesktop")),
        (os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles"), ("Docker", "Docker")),
    )
    return tuple(Path(root).joinpath(*parts, "resources", "bin") for root, parts in roots if root)


def _windows_docker_programs() -> tuple[str, ...]:
    """Absolute `docker.exe` paths to try when the live PATH holds none.

    Returns `()` the moment plain `docker` resolves, so a healthy machine never
    reads the registry, never stats a directory, and never pays for a second
    process spawn — the cost of this whole mechanism falls only on the run that
    is actually broken.
    """
    if _which("docker") is not None:
        return ()
    found: list[str] = []
    try:
        on_disk = _which("docker", _registry_search_path())
    except (ImportError, OSError) as exc:
        logger.debug(f"could not re-read the Windows PATH from the registry: {exc}")
        on_disk = None
    if on_disk:
        found.append(on_disk)
    for directory in _windows_docker_bins():
        candidate = str(directory / "docker.exe")
        if candidate not in found and Path(candidate).is_file():
            found.append(candidate)
    if found:
        logger.info(f"docker is not on this process's PATH; found it at {found[0]}")
    return tuple(found)


def docker_programs() -> tuple[str, ...]:
    """Every way of naming the `docker` CLI worth trying on this host, best first.

    Off Windows this is exactly `("docker",)` and nothing else runs: PATH means
    the same thing to a running process as it does to the shell that started it,
    so `shutil.which` is correct and sufficient there.

    On Windows it is not, and the gap is structural rather than unlucky. A
    process inherits its environment at creation and Windows never updates a
    live process's copy, so the launcher that just ran Docker Desktop's silent
    installer cannot see the PATH entry that installer wrote. Reproduced in one
    process (2026-08-23): with the engine fully up, stripping the docker
    directory out of `os.environ["PATH"]` makes `shutil.which("docker")` return
    None; resolving against the registry's PATH in the same breath still returns
    `...\\DockerDesktop\\resources\\bin\\docker.EXE`.

    What that cost the user: `ensure_docker()` would install Docker Desktop,
    start it, watch it come up, poll `docker info` for the full 180 seconds
    without ever finding the binary, and finish with "Docker Desktop was
    installed but its engine has not answered yet — open Docker Desktop, wait
    for 'Engine running', then try again". The engine WAS running. Restarting
    the launcher was the only way through, so a first run could not finish
    unattended no matter what else was fixed.

    The registry is asked before the hardcoded install directories, inverting
    the obvious order, because the registry is what the installer actually
    wrote: it is right for a custom `--installdir` and right for whatever
    layout the next Docker Desktop ships, while the hardcoded list was measured
    wrong on the only real machine available (see `_windows_docker_bins()`).
    Both cost microseconds next to the process spawn they precede.

    Nothing is cached. The value is meant to change underneath us — that is the
    entire point — so `_wait_docker_ready()`'s poll re-asks every few seconds
    and picks up the PATH entry the installer writes mid-wait.
    """
    if detect() != "windows":
        return ("docker",)
    return ("docker", *_windows_docker_programs())


DOCKER_CLI_MISSING_HELP = (
    "Docker could not be found on this machine. Install Docker Desktop "
    "(Windows/macOS) or Docker Engine (Linux) and try again — and if it is "
    "already installed, open Docker Desktop once, wait for 'Engine running', "
    "and try again then."
)
"""What to tell the user when `docker_program()` comes back empty.

One sentence, one home. Each module that has to say it raises its own error
type, so the wording is the only part they can share (style-guide §4).

Modules that raise it: `apply`, `console`, `docker`, `git`, `maintenance`.

That line is checked, not trusted. It read "Four modules … `docker`, `console`,
`git` and `apply`" for as long as it took `maintenance` to start raising this
and nobody to notice, and `console.py` carried a matching "the fourth module
that has to say this" comment that went wrong with it (audit, 2026-08-24). A
comment that enumerates code goes stale in silence, so `test_platform.py` asks
the package which modules reference this constant and compares the two as
sets — a module that joins and a module that leaves both fail it. The count
word is gone on purpose: a number is one more thing to keep true. Saying it at all is the point: a
launcher that cannot find the CLI used to fail with `FileNotFoundError:
[WinError 2] The system cannot find the file specified`, which names neither
docker nor anything the user can act on.
"""

_resolved_docker_cli: str | None = None
"""The candidate `docker_program()` settled on, or None while it has not.

Assigned at most once per process, possibly from a worker thread while the GUI
thread reads it. Two threads racing here compute the same answer from the same
registry and the same disk, so the loser's write is the winner's value; a lock
would serialize a 15ms function to buy nothing.
"""


def docker_program() -> str | None:
    """The one name to put at argv[0] of a `docker` command, or None if there is none.

    `docker_programs()` answers "everything worth trying"; a command line needs
    exactly one, so this picks the first candidate `shutil.which` can resolve.
    That is the same evidence `_windows_docker_programs()` already trusts — it
    only ever offers a path it found through `_which` or confirmed with
    `is_file()` — so nothing new is being believed here, and for the bare name
    `docker` it is the only test available short of spawning a process.

    `None` is a real answer, not a failure to compute one: this host has no
    docker CLI. Callers turn it into their own error carrying
    `DOCKER_CLI_MISSING_HELP`, which is the whole reason the return type is
    optional rather than a hopeful `"docker"` that guarantees a
    `FileNotFoundError` two lines later.

    **A hit is cached; a miss never is**, and that asymmetry is the design.
    Measured on this Windows 11 box (2026-08-23, 200 iterations each):
    `docker_programs()` costs 7.5ms when docker is on the live PATH — a full
    PATHEXT walk of a 988-character PATH — and 14.7ms when it is not, because
    that is the run that reads both registry hives and stats the fallback
    directories, and it logs an INFO line every single time. One `docker
    inspect` costs 308ms, so the resolution is 2-5% of a command; but
    `wait_ready()` issues five docker commands per poll, polls every 2s, and
    runs for up to 480s — 1200 commands, i.e. ~18s of pure PATH scanning and
    1200 identical log lines for one server start. Resolving once removes both.

    Not caching the miss is what keeps the caching honest, because "no docker
    yet" is precisely the state `ensure_docker()` exists to end. A launcher
    started on a bare Windows box resolves nothing, caches nothing, and pays
    14.7ms per call for as long as that is true; the first call after the
    silent installer writes its PATH entry finds `docker.exe` in a hive this
    process never re-read at startup and pins it for the rest of the run. That
    is the exact sequence this whole mechanism was built for, and a cached
    `None` would have broken it.

    The case the cache does not follow is the reverse one — Docker uninstalled
    while the launcher is open — where a pinned absolute path stops resolving.
    `docker._docker()` turns the resulting `OSError` into the same "Docker
    could not be found" answer, so the user is told the truth; they are just
    told it via a path that used to work.
    """
    global _resolved_docker_cli
    if _resolved_docker_cli is not None:
        return _resolved_docker_cli
    for candidate in docker_programs():
        if _which(candidate) is not None:
            _resolved_docker_cli = candidate
            return candidate
    return None


def docker_ready(run: RunCmd | None = None) -> bool:
    """True if `docker info` succeeds (daemon reachable); False if binary/daemon is missing.

    Tries each of `docker_programs()` in turn, which is one plain `docker` off
    Windows and one plain `docker` plus any off-PATH `docker.exe` on it — see
    there for why the second is not optional in the run that installs Docker.

    A candidate that cannot be started at all is a `FileNotFoundError` from
    `subprocess`, not an answer, so it is logged and the next one is tried;
    swallowing it silently is how the plain-`docker` failure went unexplained
    for a full 180-second poll.
    """
    do = run if run is not None else (lambda argv: runner.run(argv))
    for program in docker_programs():
        try:
            if do([program, "info"]).returncode == 0:
                return True
        except OSError as exc:
            logger.debug(f"could not start {program}: {exc}")
    return False


# ------------------------------------------------------- the docker group ask
# Joining the `docker` group is the one thing provisioning does that changes
# what the machine can be made to do: membership is root-equivalent, because
# `docker run -v /:/mnt --rm -it alpine chroot /mnt sh` edits any file on the
# host. The roadmap's Phase 6 preamble makes it a consented step on every
# install path. It was not one here until 2026-08-24: `_ensure_docker_linux()`
# ran it under `sudo -n` among the package steps, so on any passwordless-sudo
# box — which is both of this project's own Linux test VMs — the launcher
# granted itself root before the installer script got as far as its own polite
# question, and that question then found the user already a member and never
# asked. Proven in a throwaway ubuntu:24.04 container: `dad` went from groups
# `['dad']` to `['dad', 'docker']` with nobody asked.

DOCKER_GROUP_QUESTION = (
    "Yu'lon can add '{user}' to the docker group, so it can use Docker without asking "
    "for your password every time.\n"
    "\n"
    "Heads up: membership in the docker group is effectively full root access on this "
    "machine. A docker user can, for example, mount your entire disk inside a container "
    "and change any file.\n"
    "\n"
    "If you say yes: you'll need to log out and back in once before it takes effect, "
    "then click Install again.\n"
    "If you say no: Yu'lon still installs Docker Engine, but it runs docker directly "
    "(never through sudo), so it cannot install or manage a server here until you join "
    "the group yourself: sudo usermod -aG docker {user}, then log out and back in.\n"
    "\n"
    "Yu'lon never creates passwordless sudo rules and never changes the docker socket's "
    "permissions.\n"
    "\n"
    "Add '{user}' to the docker group (grants root-equivalent access)? (y/n): "
)
"""The consent question, whose last line matches the six bash scripts' wording.

The `(y/n)` is load-bearing twice over. `ui.widgets.prompt.is_secret()` reads
it to leave the answer field unmasked — without it the consent answer arrives
as a password box — and it is the same token the installers' own
`docker_group_consent()` prints, so a user who meets both questions meets one
question asked the same way. `test_prompt.py` pins the first of those.
"""

DOCKER_GROUP_DECLINED_STEP = (
    "You said no to joining the docker group, so Yu'lon cannot use Docker on this machine "
    "yet. To change your mind later: sudo usermod -aG docker {user}, then log out and back in."
)
"""What declining costs. It used to add "Docker Engine is installed" — unconditionally.

That is a claim about a command that may not have run: on a box where `sudo -n`
has no ticket, every package step lands in `skipped` and the report then
contradicted itself inside one tuple, promising an installed engine two lines
under "Some steps needed a password". The engine's fate is reported by the
steps themselves, which is the only place that knows it (review, 2026-08-24).
"""

DOCKER_GROUP_JOIN_FAILED_STEP = (
    "You said yes, but adding {user} to the docker group did not work — see the step above for "
    "why. Until it does, Yu'lon cannot use Docker here. To do it yourself: "
    "sudo usermod -aG docker {user}, then log out and back in."
)
"""Consent is not the same event as the join succeeding, and the report must not merge them.

`usermod` runs under `sudo -n` exactly like the package steps, and fails for
the same reasons: no cached ticket by the time it runs, a sudoers rule scoped
to `apt-get` but not `usermod`, or no `docker` group because the engine install
itself failed. The old code printed "log out and back in, then click Install
again" on the strength of the ANSWER, so a user whose join had failed was told
to log out, log back in, click Install, and meet the identical failure with no
explanation (review, 2026-08-24).
"""

DOCKER_GROUP_UNASKED_STEP = (
    "Skipped joining the docker group: it grants root-equivalent access, and with nobody to "
    "ask Yu'lon never makes that change. To do it yourself: sudo usermod -aG docker {user}, "
    "then log out and back in."
)

DOCKER_GROUP_RELOGIN_STEP = (
    "Log out and back in (or run `newgrp docker`) so {user} can use Docker without sudo, "
    "then click Install again."
)
"""Shown only where it is true: after a join that ran, or for an existing member.

It used to be unconditional, which made it advice on the two paths where no
group change had happened at all. The second half of the sentence is not
padding either: `usermod` does not change a running process's supplementary
groups, so `docker_ready()` stays false for the rest of this run even when the
answer was yes. Saying otherwise would promise an install that cannot start.
"""


def _explicit_yes(reply: str | None) -> bool:
    """Only a deliberate yes is consent. A dismissed dialog is not.

    The same reading `make_responder()` applies to the installers' version of
    this question, deliberately written the same way here: silence, an empty
    string and a closed dialog all mean no, because refusing a privilege change
    is recoverable and visible while granting one by accident is neither.
    """
    return reply is not None and reply.strip().lower() in ("y", "yes")


def _docker_group_member(do: RunCmd, user: str) -> bool:
    """Is `user` already in the `docker` group? False when it cannot be asked.

    Exact token match, not a substring: a machine with a `dockerd` or
    `docker-users` group must not read as a member and skip the question.

    Unreadable answers False, so an unaskable machine is asked rather than
    assumed — the safe direction, since the cost of a redundant question is a
    click and the cost of a wrong skip is a silent escalation. `id` needs no
    privilege, so this never goes through sudo.
    """
    try:
        proc = do(["id", "-nG", user])
    except OSError as exc:
        logger.info(f"could not read {user}'s groups: {exc}")
        return False
    if proc.returncode != 0:
        return False
    return "docker" in proc.stdout.split()


def linux_package_manager(
    which: Callable[[str], str | None] | None = None,
) -> PackageManager | None:
    """Which package manager this Linux has (pacman → Arch/SteamOS, apt, dnf, zypper)."""
    find = which if which is not None else _which
    if find("pacman"):
        return "pacman"
    if find("apt-get"):
        return "apt"
    if find("dnf"):
        return "dnf"
    if find("zypper"):
        return "zypper"
    return None


def docker_engine_commands(pm: PackageManager, *, steamos: bool) -> list[list[str]]:
    """The (sudo-less) commands that install + enable Docker Engine via `pm`.

    **The docker-group join is deliberately not in this list**, and putting it
    back is not a one-line append: it would need `user` again, which is why the
    parameter is gone. The argv that grants root-equivalent access is built in
    exactly one place — inside `_ensure_docker_linux()`'s consent branch — so
    there is no second construction site that a gate could be added to and then
    forgotten. It lived here, ungated, from 5.1 until 2026-08-24.
    """
    install: list[list[str]]
    if pm == "pacman":
        install = [["pacman", "-Sy", "--noconfirm", "docker", "docker-compose"]]
        if steamos:
            install = [["steamos-readonly", "disable"], *install, ["steamos-readonly", "enable"]]
    elif pm == "apt":
        install = [
            ["apt-get", "update"],
            # docker-buildx too: `docker.io` ships no BuildKit plugin and the server
            # images are built with `compose up --build` (live gate, Ubuntu 24.04).
            ["apt-get", "install", "-y", "docker.io", "docker-compose-v2", "docker-buildx"],
        ]
    elif pm == "dnf":
        install = [["dnf", "-y", "install", "moby-engine", "docker-compose"]]
    else:
        install = [["zypper", "--non-interactive", "install", "docker", "docker-compose"]]
    return [*install, ["systemctl", "enable", "--now", "docker"]]


# ------------------------------------------------------------------ downloads
# The Windows/macOS provisioning paths fetch a Docker Desktop installer — 629 MB
# (659,189,680 bytes, measured 2026-08-23) — and then run it ELEVATED. So the
# transfer has to be certificate-verified, and on the box this was measured on
# the obvious way to do that does not work.
#
# Measured by hand on a fresh Windows 11 install (2026-08-22, Python 3.12.10 /
# OpenSSL 3.0.16): `urllib.request.urlopen` aborted after 0.4 s with
# `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate`, and
# the user was handed the "go download Docker Desktop yourself" step this product
# exists to remove. On the same box github.com, raw.githubusercontent.com and
# pypi.org all verified fine; only desktop.docker.com did not, and
# `ssl.get_default_verify_paths().cafile` was None with 18 CA certs in the store.
#
# Why one host and not the others: Windows ships a small root set and pulls the
# rest ON DEMAND, through the CryptoAPI automatic root update, while schannel is
# building a chain. OpenSSL — which Python's `ssl` uses — reads a SNAPSHOT of the
# same store and never triggers that fetch, so it only ever sees roots that
# already happen to be materialized. desktop.docker.com chains to Amazon RSA
# 2048 M01 -> Amazon Root CA 1, which was not among the 18; github.com chains to
# Sectigo/USERTrust, which was. (Chains re-checked from a healthy Windows box,
# 2026-08-23: 58 CA certs there, and both hosts verify.)
#
# So the fix is to give the transfer a root set that is actually complete, in
# this order:
#   1. The OS-shipped `curl` (System32 on Windows 10 1803+, /usr/bin/curl on
#      macOS/Linux), pinned by absolute path. It is built against schannel /
#      Secure Transport, i.e. the OS trust engine WITH the on-demand root fetch,
#      so it sees the roots OpenSSL cannot — and it sees an enterprise root
#      installed by a TLS-intercepting proxy, which a bundled CA file
#      structurally cannot. It also brings resume and retry for the 629 MB.
#   2. `urllib` against the OS root store WITH certifi's Mozilla bundle added on
#      top (`requirements.txt` ships certifi); the OS store alone when certifi is
#      not importable or its bundle cannot be read. In-process, portable, and the
#      backstop for a box with no curl (Windows before 1803) or a curl that will
#      not run. Keeping the OS roots in the set is what preserves the
#      enterprise-root case in step 1 for this transport too — see
#      `verify_context()`.
#
# There is no third step. Verification is never turned off here: an unverified
# download of an executable that is about to be run with elevation is a
# supply-chain hole, not a fallback. If both transports fail, the caller gets
# both errors and a manual step (`_MANUAL_ROOT_CERTS`).

_DOWNLOAD_CHUNK_BYTES = 1 << 20

# curl exit codes worth telling apart. 33 = "HTTP server doesn't seem to support
# byte ranges", i.e. a resume that can never finish; 60/77 are peer-certificate
# and CA-store failures, the ones that mean "could not verify" rather than "the
# network is down" — a distinction the user's next step depends on.
_CURL_NO_RANGE_EXIT = 33
_CURL_VERIFY_EXITS = frozenset({60, 77})

# How far `_is_verification_failure()` follows `.reason`. urllib wraps once; the
# rest of the budget is for an injected opener that wraps again, and the bound
# itself is what makes a self-referential chain terminate.
_REASON_UNWRAP_LIMIT = 4

_CURL_ARGS: tuple[str, ...] = (
    "--fail",
    "--location",
    "--silent",
    "--show-error",
    # A redirect must not be able to downgrade an executable download to plain
    # HTTP; that is an unverified fetch wearing a different hat.
    "--proto",
    "=https",
    "--proto-redir",
    "=https",
    "--connect-timeout",
    "30",
    # Give up on a STALLED transfer (under 1 KB/s for a minute), not on a merely
    # slow one: 629 MB over a bad link is not a failure, and a wall-clock
    # `--max-time` would call it one.
    "--speed-limit",
    "1024",
    "--speed-time",
    "60",
    "--retry",
    "3",
    "--retry-delay",
    "2",
    "--retry-connrefused",
    # Continue where the last attempt stopped. Measured against the real
    # installer (2026-08-23): an attempt cut off at 104,574,603 bytes resumed and
    # asked the CDN for the remaining 554,615,077 of 659,189,680. A missing or
    # empty output file resumes from 0, and an already-complete one exits 0
    # having transferred nothing.
    "--continue-at",
    "-",
)


class DownloadError(OSError):
    """A download failed and was NOT retried over an unverified connection.

    An `OSError` so the provisioning paths that already turn a failed download
    into a reported manual step keep working unchanged. `verification` is True
    when the failure was the certificate rather than the network, because the
    user's next step differs: a missing root is theirs to fix (Windows Update),
    a dead link is not.
    """

    def __init__(self, message: str, *, verification: bool = False) -> None:
        super().__init__(message)
        self.verification = verification


class HttpResponse(Protocol):
    """The slice of an `urlopen` result the downloader touches.

    A Protocol, not the concrete `http.client.HTTPResponse`, so tests can hand
    `_download_urllib` a fake with no socket behind it — and so the `Any` that
    typeshed gives `urlopen` is narrowed right at that boundary (style-guide §2).
    """

    def geturl(self) -> str: ...

    def getheader(self, name: str, default: str | None = None) -> str | None: ...

    def read(self, amt: int = ...) -> bytes: ...

    def close(self) -> None: ...


UrlOpener = Callable[[urllib.request.Request], HttpResponse]


def _os_curl() -> Path | None:
    """The OS-shipped curl, by absolute path — never whatever `curl` PATH answers with.

    Windows 10 1803+ ships `%SystemRoot%\\System32\\curl.exe` built against
    schannel; macOS ships `/usr/bin/curl` against the system trust store. Both
    use the OS trust engine, which is the whole point (see the block above).
    Resolving through PATH instead would let any curl earlier in it decide how an
    about-to-be-elevated installer gets verified, and would happily pick a build
    against a stale vendored CA file — on the dev box this was written on, PATH
    answers with Git's mingw curl before System32's.
    """
    if detect() == "windows":
        system_root = os.environ.get("SystemRoot") or "C:\\Windows"
        candidate = Path(system_root) / "System32" / "curl.exe"
    else:
        candidate = Path("/usr/bin/curl")
    return candidate if candidate.exists() else None


@functools.lru_cache(maxsize=1)
def verify_context() -> ssl.SSLContext:
    """A verifying TLS context trusting the OS root store AND certifi's — the union, not one.

    `ssl.create_default_context()` already means verify + check hostname, and on
    its own it loads the OS roots. What it cannot fix is that OpenSSL only ever
    reads a SNAPSHOT of the Windows store while Windows materializes most roots
    on demand through CryptoAPI: the fresh Windows 11 box the block above was
    written for had 18 of them and could not chain desktop.docker.com to Amazon
    Root CA 1. certifi (Mozilla's bundle) carries the ones that are missing.

    Correcting the first version of this function (review finding, 2026-08-23):
    it built the context as `create_default_context(cafile=certifi.where())`,
    which does not WIDEN the OS store — given a `cafile`, `create_default_context()`
    takes that arm and never calls `load_default_certs()`, so the result trusted
    certifi's roots and nothing else. Measured here: the OS store holds 58 CA
    certs, certifi 2026.07.22 holds 121, and 33 of the 58 are absent from certifi
    (DigiCert Global Root CA and Baltimore CyberTrust Root among them) — as is,
    by construction, every root an administrator installed, which is how a
    corporate TLS-inspecting proxy or an internal CA is trusted at all. Loading
    the OS store first and calling `load_verify_locations()` afterwards ADDS to
    it instead of replacing it: 154 CA certs, with both sets contained in the
    result.

    An unreadable certifi bundle degrades to the OS store rather than raising.
    Raising would turn a packaging fault — `cacert.pem` not collected into the
    PyInstaller build — into "could not determine the public IP (offline?)", the
    exact misdiagnosis the rest of this section exists to remove. The OS store
    alone still verifies every connection; it is the narrower failure, and the
    log line says which one happened.

    Nothing here relaxes verification, and there is no branch that can: every
    path returns a `create_default_context()` result with roots added to it and
    no other setting touched.

    Public, and imported by `manifest_store` and `update`, because the root-store
    gap is a fact about the OS this process runs on — the same thing `detect()`
    and `config_dir()` are about — and every HTTPS call in the app has it. Both
    of those modules already sit above this one in the import graph (this one
    imports only `runner` and `log`), so there is no cycle to create; giving the
    context its own module would only move the measurements above away from the
    `download_verified()` code they were taken for.

    Cached because assembling it is not free: measured on this dev box, 14 ms for
    the OS store alone, 193 ms for certifi alone and 211 ms for the union of the
    two. One `urlopen` per manifest file means a full refresh of the WotLK tree
    is 45 GETs, so building a context per call would have put ~9.5 s of
    certificate parsing into it. Sharing one context across connections is the
    normal way to use `ssl` — a context holds no per-connection state — and the
    root set it reads cannot change inside one run of the app.
    """
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        logger.debug("certifi is not importable; using the OS root store alone")
        return context
    try:
        context.load_verify_locations(cafile=certifi.where())
    except Exception as exc:
        # Deliberately broader than OSError, and `where()` is inside the try for
        # the same reason: locating the bundle can fail in ways that are not
        # OSError. A frozen build whose certifi module spec is broken makes
        # `importlib.resources.files("certifi")` raise AttributeError (measured).
        # The caller that matters here is `detect_public_ip`, which catches only
        # (OSError, ValueError) -- so anything else does not become a wrong
        # diagnosis, it becomes an unhandled traceback out of `networking.plan()`,
        # which is worse than the "offline?" lie this function exists to prevent.
        # Degrading to the OS store is correct for every one of these failures:
        # the context still verifies, it just knows fewer roots
        # (review finding, 2026-08-23).
        logger.warning(
            "certifi's bundle could not be used (%s); continuing with the OS root store alone. "
            "Connections are still fully verified, but a host whose root this machine has not "
            "materialized will fail to verify.",
            exc,
        )
    return context


def _open_url(request: urllib.request.Request) -> HttpResponse:
    """`urlopen` with the verifying context from `verify_context()`."""
    resp: HttpResponse = urllib.request.urlopen(request, timeout=60.0, context=verify_context())
    return resp


def _expected_total(resp: HttpResponse) -> int | None:
    """How many bytes the finished file should have, or None if the server won't say.

    On a resumed request `Content-Length` counts only the remaining range, so the
    authoritative total is the tail of `Content-Range: bytes 1-2/TOTAL`.
    """
    content_range = resp.getheader("Content-Range")
    if content_range:
        total = content_range.rsplit("/", 1)[-1].strip()
        return int(total) if total.isdigit() else None
    length = resp.getheader("Content-Length")
    return int(length) if length and length.strip().isdigit() else None


def _download_curl(url: str, part: Path, curl: Path, do: RunCmd) -> None:
    """Fetch `url` into `part` with the OS curl, resuming whatever is already there."""
    argv = [str(curl), *_CURL_ARGS, "--output", str(part), url]
    try:
        proc = do(argv)
        if proc.returncode == _CURL_NO_RANGE_EXIT and part.exists():
            # The CDN answered the resume request with "no ranges here". Keeping
            # the partial would make every future attempt fail the same way, so
            # drop it and take the whole file again.
            logger.info(f"{url}: server refuses byte ranges; restarting the download")
            part.unlink()
            proc = do(argv)
    except OSError as exc:
        raise DownloadError(f"{curl.name} could not run: {exc}") from exc
    if proc.returncode != 0:
        raise DownloadError(
            f"{curl.name} exited {proc.returncode}: {proc.stderr.strip() or url}",
            verification=proc.returncode in _CURL_VERIFY_EXITS,
        )


def _download_urllib(url: str, part: Path, open_url: UrlOpener) -> None:
    """Fetch `url` into `part` in-process, resuming with a `Range` request if it can.

    A server that ignores the `Range` header answers 200 with the whole body and
    no `Content-Range`; that restarts the file rather than appending a second
    copy of it onto the first. The finished size is checked against what the
    server said, so a connection cut mid-body leaves a `.part` to resume from and
    never a short file that gets renamed into place and run.
    """
    start = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "yulon"}
    if start:
        headers["Range"] = f"bytes={start}-"
    with closing(open_url(urllib.request.Request(url, headers=headers))) as resp:
        final = resp.geturl()
        if not final.startswith("https://"):
            raise DownloadError(f"{url} redirected to {final}, which is not HTTPS")
        resumed = resp.getheader("Content-Range") is not None
        expected = _expected_total(resp)
        written = start if resumed else 0
        with part.open("ab" if resumed else "wb") as out:
            while chunk := resp.read(_DOWNLOAD_CHUNK_BYTES):
                out.write(chunk)
                written += len(chunk)
    if expected is not None and written != expected:
        raise DownloadError(f"{url}: transfer ended at {written} of {expected} bytes")


def _is_verification_failure(exc: OSError) -> bool:
    """True when `exc` means 'could not verify the certificate', not 'could not connect'.

    The `.reason` walk is the whole function, not padding (review finding,
    2026-08-23): `urllib.request.urlopen` NEVER lets an
    `ssl.SSLCertVerificationError` escape. `AbstractHTTPHandler.do_open` catches
    the `OSError` the handshake raises and re-raises it as
    `urllib.error.URLError(err)`, keeping the original in `.reason`. So the bare
    `isinstance` check this shipped with answered False for every exception
    `_http_get_text()` or `_download_urllib()` can raise, which left the
    certificate branch of `networking.plan()` unreachable — it went on printing
    "could not determine the public IP (offline?)" — and left `_MANUAL_ROOT_CERTS`
    reachable only through `_download_curl`'s exit code, i.e. silent on a box
    with no OS curl. Measured against a self-signed server on 127.0.0.1:
    `URLError(SSLCertVerificationError(...))`, one level deep.

    That `raise URLError(err)` is the only site in `urllib` that wraps an
    arbitrary `OSError`, so the stdlib never nests deeper than one; the loop
    rather than a single unwrap is for the `open_url`/`http_get` seams, which a
    caller may wrap again, and it is bounded so a self-referential `.reason`
    cannot spin.
    """
    current: BaseException = exc
    # +1 because the limit counts HOPS, and the exception handed in has been
    # followed zero times: without it a certificate error at the limit's own
    # depth answers False and the constant overstates the code by one.
    for _ in range(_REASON_UNWRAP_LIMIT + 1):
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if isinstance(current, DownloadError) and current.verification:
            return True
        # `HTTPError.reason` is a str, and a plain OSError has no `.reason` at
        # all; either way there is nothing further to unwrap.
        reason = getattr(current, "reason", None)
        if not isinstance(reason, BaseException):
            return False
        current = reason
    return False


def download_verified(
    url: str,
    dest: Path,
    *,
    run: RunCmd | None = None,
    find_curl: Callable[[], Path | None] | None = None,
    open_url: UrlOpener | None = None,
) -> Path:
    """Download `url` to `dest` over a verified connection, or fail loudly.

    Tries the OS-shipped curl first and `urllib` (OS roots + certifi) second; the
    long comment above this function says why that order, and why there is no
    third attempt. Raises `DownloadError` — an `OSError`, so existing callers
    keep reporting it as a skipped step — with both transports' messages and
    `verification` set when the certificate, not the network, was the problem.

    Re-downloading is avoided at two granularities, because the file in question
    is 629 MB and the old code fetched all of it again on every attempt:
    a completed `dest` is reused as-is, and an interrupted attempt leaves a
    `<dest>.part` that the next run resumes from. The trade-off of the first is
    staleness — the installer URL is "latest", so a cached file can be an older
    Docker Desktop than the one currently published. That is the cheap side of
    the trade: Docker Desktop updates itself on first run, and deleting the file
    forces a fresh download. The alternative, revalidating the size against the
    server on every run, costs a request over the very TLS path that is broken on
    the box this was written for.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        logger.info(f"reusing the file already downloaded at {dest}")
        return dest
    part = dest.with_name(dest.name + ".part")
    do: RunCmd = run if run is not None else (lambda argv: runner.run(argv))
    failures: list[str] = []
    verification: list[bool] = []

    curl = (find_curl if find_curl is not None else _os_curl)()
    if curl is not None:
        try:
            _download_curl(url, part, curl, do)
            part.replace(dest)
            return dest
        except OSError as exc:
            logger.warning(f"{curl.name} could not fetch {url}: {exc}")
            failures.append(f"{curl.name}: {exc}")
            verification.append(_is_verification_failure(exc))

    try:
        _download_urllib(url, part, open_url if open_url is not None else _open_url)
        part.replace(dest)
        return dest
    except OSError as exc:
        logger.warning(f"urllib could not fetch {url}: {exc}")
        failures.append(f"urllib: {exc}")
        verification.append(_is_verification_failure(exc))

    raise DownloadError(
        f"{url} could not be downloaded over a verified connection "
        f"({'; '.join(failures)}). Nothing was fetched unverified.",
        verification=any(verification),
    )


def _download_manual_steps(exc: OSError) -> tuple[str, ...]:
    """What to tell the user after a failed installer download."""
    if _is_verification_failure(exc):
        return (_MANUAL_ROOT_CERTS, _MANUAL_DOCKER_DESKTOP)
    return (_MANUAL_DOCKER_DESKTOP,)


def _wait_docker_ready(
    run: RunCmd, timeout: float, poll: float, cancel: threading.Event | None = None
) -> bool:
    """Poll `docker_ready(run)` until it answers, the timeout passes, or `cancel` is set.

    `cancel` lets a caller interrupt the up-to-`timeout` poll (the installer
    passes its stop event through), so a UI "Stop" during Docker provisioning
    does not leave a worker thread sleeping for minutes while the window tears
    down (review finding, 2026-08-20: a QThread destroyed while its worker is
    still in this poll aborts the process).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if docker_ready(run):
            return True
        if cancel is not None and cancel.is_set():
            return False
        time.sleep(poll)
    return docker_ready(run)


def _c_locale_env() -> dict[str, str]:
    """This process's environment with the messages locale pinned to C.

    Provisioning classifies a skipped step by reading `sudo`'s own stderr for
    "a password is required", and `sudo` is gettext-translated: on a Danish
    desktop it says "adgangskode", on a German one "Passwort". Without this the
    password case falls into the generic bucket for everyone outside an English
    locale, and they are handed a raw exit code instead of the one instruction
    that would fix it.

    This codebase already knew: `ui/widgets/prompt.py`'s `_NOT_SECRET` regex was
    rewritten into a deny-list for exactly this reason, and its comment lists
    the same translations. Pinning the child's locale is the other half of that
    lesson — read a machine's answer in a language you chose, not the one it
    happens to be set to (review, 2026-08-24).

    `LC_ALL` rather than `LANG`, because `LC_ALL` overrides every category and
    a user with `LC_MESSAGES` set would otherwise still get translated text.
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANGUAGE"] = ""
    return env


def _run_steps(
    do: RunCmd, commands: list[list[str]], *, sudo: bool, dry_run: bool
) -> tuple[list[str], list[str]]:
    done: list[str] = []
    skipped: list[str] = []
    for cmd in commands:
        shown = " ".join(cmd)
        if dry_run:
            skipped.append(f"(dry run) {shown}")
            continue
        argv = ["sudo", "-n", *cmd] if sudo else cmd
        try:
            proc = do(argv)
        except OSError as exc:
            skipped.append(f"{shown}: {exc}")
            continue
        if proc.returncode == 0:
            done.append(shown)
        else:
            skipped.append(f"{shown}: exit {proc.returncode} {proc.stderr.strip()}")
    return done, skipped


def ensure_docker(
    *,
    run: RunCmd | None = None,
    which: Callable[[str], str | None] | None = None,
    download: Downloader = download_verified,
    dry_run: bool = False,
    user: str | None = None,
    wait_seconds: float = _DOCKER_READY_TIMEOUT_SECONDS,
    cancel: threading.Event | None = None,
    ask: runner.Prompter | None = None,
) -> ProvisionReport:
    """Make sure a Docker daemon is reachable, installing what the OS needs (README §3b).

    Linux: Docker Engine through the distro package manager (under `sudo -n`; a
    password-needing sudo is a reported skip with the commands to paste). The
    docker-group join is asked for through `ask` BEFORE anything privileged
    runs, and declined whenever there is nobody to ask; the answer is on the
    report as `docker_group`. Windows and macOS ignore `ask` — Docker Desktop
    manages its own access there and neither path touches a Unix group. Windows:
    WSL2 (`ensure_wsl2()`) then Docker Desktop (download + silent install,
    elevated), then start it at wherever `find_docker_desktop()` says it is.
    macOS: Docker Desktop (download .dmg, copy Docker.app, open it).
    Returns a `ProvisionReport`; with `dry_run=True` nothing runs and the report
    lists every step as skipped so the UI can show the plan. `cancel`, when set,
    interrupts the ready-poll early (the poll still returns the latest check).
    """
    # The default runner pins the messages locale — see `_c_locale_env()`. An
    # injected `run` is the caller's business and is left alone.
    do: RunCmd = run if run is not None else (lambda argv: runner.run(argv, env=_c_locale_env()))
    current = detect()
    if docker_ready(do):
        logger.info("ensure_docker(): daemon already reachable")
        return ProvisionReport(current, done=("docker already running",), docker_ready=True)
    if current == "linux":
        return _ensure_docker_linux(
            do, which, dry_run, _linux_user(user), wait_seconds, cancel, ask
        )
    if current == "windows":
        return _ensure_docker_windows(do, which, download, dry_run, wait_seconds, cancel)
    return _ensure_docker_macos(do, download, dry_run, wait_seconds, cancel)


def _linux_user(explicit: str | None) -> str:
    """Whose name goes in the consent dialog, and whose account gets the group.

    `SUDO_USER` names the INVOKER, and that is only the right answer when this
    process is actually running as root — which is the `sudo yulon` case it was
    added for. Under `sudo -u alice yulon` the process runs as **alice** while
    `SUDO_USER` says **bob**: the old chain offered bob root-equivalent access
    he never asked for, joined an account that is not the one making the docker
    calls, and left alice still unable to use Docker. Group membership is
    evaluated against the calling process's own credentials, so that join was
    both wrong and useless.

    So `SUDO_USER` is trusted only behind an effective-uid check, and the
    process's real identity is preferred over any environment variable — the
    environment is what an escalation tool rewrites, `geteuid()` is not.
    `DOAS_USER` is read in the same breath because `doas` exports it and
    otherwise a `doas yulon` lands on "root" the same way (unverified from
    this side — no `doas` box here; a claim to check).

    Found by a review that held it after the author had held it as too narrow:
    the `doas` half needs a tool this audience does not use, the `sudo -u` half
    needs nothing at all (2026-08-24).
    """
    if explicit:
        return explicit
    geteuid = getattr(os, "geteuid", None)
    euid = geteuid() if geteuid is not None else None
    if euid == 0:
        for named_by in ("SUDO_USER", "DOAS_USER"):
            invoker = os.environ.get(named_by)
            if invoker:
                return invoker
    if euid is not None:
        try:
            # Imported dynamically for the same reason `_registry_search_path()`
            # imports `winreg` that way, and the reason `runner.open_pty()`
            # fetches `os.openpty` that way: the module is POSIX-only and this
            # file is type-checked for BOTH platforms.
            #
            # THE TWO OBVIOUS SPELLINGS ARE EACH RED WHERE THE OTHER IS GREEN.
            # `import pwd` + a direct call is `Module has no attribute
            # "getpwuid"` on Windows, where the stub does not resolve. Adding
            # `# type: ignore[attr-defined]` fixes that and is then an
            # `unused-ignore` error on Linux, where it does — `pyproject.toml`
            # sets `warn_unused_ignores = true`. This project is developed on
            # Windows and its CI runs Linux, so each spelling passes for its
            # author and fails for everyone else; both have now shipped and
            # both have been reverted (2026-08-24). A dynamic import is `Any`,
            # so neither checker has anything to say, and `str()` is what keeps
            # the promise this signature makes.
            pwd = importlib.import_module("pwd")
            return str(pwd.getpwuid(euid).pw_name)
        except (ImportError, KeyError):
            logger.info(f"no passwd entry for uid {euid}; falling back to the environment")
    # `deck` last: it is the SteamOS default this project targets, and a wrong
    # guess here is a name in a question rather than an action.
    return os.environ.get("USER") or os.environ.get("USERNAME") or "deck"


def _ensure_docker_linux(
    do: RunCmd,
    which: Callable[[str], str | None] | None,
    dry_run: bool,
    user: str,
    wait_seconds: float,
    cancel: threading.Event | None = None,
    ask: runner.Prompter | None = None,
) -> ProvisionReport:
    """Install Docker Engine, and join the docker group only if asked and told yes.

    The order matters and is the whole fix. Consent is settled BEFORE the first
    privileged command, not after the package install: the roadmap requires the
    question "before any docker-group join or privileged provisioning step",
    and asking first also puts the dialog in front of someone who just clicked
    Install rather than several minutes into an `apt-get`.

    Declining the group is not declining Docker. The engine is installed either
    way — that is the disclosed part of "the app installs everything" — and
    what the user keeps is the choice about their own machine's security.
    """
    pm = linux_package_manager(which)
    if pm is None:
        return ProvisionReport(
            "linux",
            manual_steps=(
                "No supported package manager (pacman/apt/dnf/zypper) found. Install Docker "
                "Engine by hand: https://docs.docker.com/engine/install/",
            ),
        )

    consent = _settle_docker_group(do, user, dry_run, cancel, ask)

    commands = docker_engine_commands(pm, steamos=is_steamos())
    done, skipped = _run_steps(do, commands, sudo=True, dry_run=dry_run)
    joined_ok = False
    if consent == "granted":
        # The one place this argv is built. `docker_engine_commands()` no
        # longer contains it, so there is no ungated path to it at all.
        joined, refused = _run_steps(
            do, [["usermod", "-aG", "docker", user]], sudo=True, dry_run=dry_run
        )
        joined_ok = bool(joined) and not refused
        done, skipped = [*done, *joined], [*skipped, *refused]
    elif dry_run:
        skipped.append(f"(dry run) usermod -aG docker {user} (asks first)")

    ready = False if dry_run else _wait_docker_ready(do, min(wait_seconds, 30.0), 2.0, cancel)

    # What HAPPENED, not what was agreed to. `consent` answers the question;
    # whether the command that follows it worked is a separate fact, and telling
    # a user to log out and back in because they said yes — when the join failed
    # — sends them round a loop that ends in the same failure. The report now
    # carries this rather than `consent`, so the support JSON cannot claim a
    # membership the machine does not have.
    outcome: DockerGroupOutcome = (
        "join-failed" if consent == "granted" and not joined_ok else consent
    )

    manual: list[str] = []
    if outcome == "granted":
        manual.append(DOCKER_GROUP_RELOGIN_STEP.format(user=user))
    elif outcome == "join-failed":
        manual.append(DOCKER_GROUP_JOIN_FAILED_STEP.format(user=user))
    elif outcome == "declined":
        manual.append(DOCKER_GROUP_DECLINED_STEP.format(user=user))
    elif outcome == "not-asked":
        manual.append(DOCKER_GROUP_UNASKED_STEP.format(user=user))
    if skipped and not dry_run:
        # A skip is reported by its real cause, not by the likeliest one. `sudo
        # -n` announces the password case itself ("a password is required"),
        # and `_run_steps` keeps that stderr in the record — so the two have
        # always been distinguishable and the guess was never needed. Measured
        # in a container on yulon-ubuntu (2026-08-24): `systemctl` was simply
        # absent, and the user was told to re-run it in a terminal with sudo,
        # which fails identically.
        password = [s for s in skipped if "password" in s.lower()]
        other = [s for s in skipped if s not in password]
        if other:
            manual.insert(0, "Some steps did not run: " + "; ".join(other))
        if password:
            failed = "; ".join(s.split(":")[0] for s in password)
            manual.insert(
                0, f"Some steps needed a password; run them in a terminal with sudo: {failed}"
            )
    return ProvisionReport(
        "linux", tuple(done), tuple(skipped), tuple(manual), False, ready, outcome
    )


def _settle_docker_group(
    do: RunCmd,
    user: str,
    dry_run: bool,
    cancel: threading.Event | None,
    ask: runner.Prompter | None,
) -> DockerGroupOutcome:
    """Decide the docker-group question without running anything privileged.

    Every branch that is not a deliberate yes is a no. Four events, and
    deliberately THREE names for them: `already-member`, `declined`, and
    `not-asked` — which covers a cancelled run, a dry run and a run with nobody
    to ask alike, because all three mean the same thing to someone reading a
    report: the question was never put. (This docstring claimed four names for
    four events until an audit read the branches, 2026-08-24. `granted` and
    `join-failed` are the two yes-shaped outcomes and belong to the caller, not
    to this function.)

    A cancelled run never opens a dialog. A `dry_run` never opens one either —
    it exists to show a plan, and a plan that interrogates the user is not one.
    """
    if dry_run or (cancel is not None and cancel.is_set()):
        # `dry_run` shows a plan, so it runs nothing at all — not even the
        # harmless `id`. A cancelled run does not open a dialog either.
        return "not-asked"
    if _docker_group_member(do, user):
        return "already-member"
    if ask is None:
        return "not-asked"
    answer = _explicit_yes(ask(DOCKER_GROUP_QUESTION.format(user=user)))
    logger.info(f"docker group consent for {user}: {'granted' if answer else 'declined'}")
    return "granted" if answer else "declined"


def _ps_quote(value: object) -> str:
    """`value` as a PowerShell single-quoted literal (inner quotes doubled)."""
    return SINGLE_QUOTE + str(value).replace(SINGLE_QUOTE, SINGLE_QUOTE * 2) + SINGLE_QUOTE


# ------------------------------------------------------- finding Docker Desktop

DOCKER_DESKTOP_EXE = "Docker Desktop.exe"
_DOCKER_DESKTOP_SHORTCUT = "Docker Desktop.lnk"

# The install layouts to fall back on when the probe below cannot run at all —
# the same role, and the same standing, as `_windows_docker_bins()`: a guess
# kept for a box whose PowerShell is locked down, not evidence.
#
# Both `ProgramW6432` and `ProgramFiles` are listed because they disagree inside
# a 32-bit process: there `%ProgramFiles%` is the x86 folder, where a
# 64-bit-only app never is, while `%ProgramW6432%` is always the real one.
_DOCKER_DESKTOP_ROOT_VARS = ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")

# `<root>\Docker\Docker\Docker Desktop.exe` is the machine-wide layout that
# every "where is Docker Desktop" answer names. `Programs\DockerDesktop` is the
# per-user one, and is the one that was actually there: a Windows 11 PC running
# Docker Desktop 4.83.0 had the app at
# `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe` and nothing under
# Program Files at all (2026-08-23) — the same box, and the same lesson, as
# `_windows_docker_bins()`. Every shape is tried under every root; a dozen
# `is_file()` calls cost nothing next to the process spawn they follow.
_DOCKER_DESKTOP_SUBDIRS = (("Docker", "Docker"), ("Programs", "DockerDesktop"), ("Docker",))

# Registry paths worth reading, under BOTH hives. The first two are Docker
# Desktop's own (`AppPath` under `1.0` is the install folder); `App Paths` is
# the Windows mechanism that makes `Start-Process <bare name>` work for the apps
# that DO register one, and is the only thing that could ever have rescued the
# old command; `Uninstall` carries `InstallLocation`.
#
# Both hives, because on the 4.83.0 machine above a per-user install had written
# NOTHING to HKLM — no `Docker Inc.` key, no `App Paths` entry, nothing on
# PATH — and the single registry value naming the install was
# `HKCU:\...\Uninstall\Docker Desktop`'s `InstallLocation`. Reading only HKLM,
# the obvious hive for an installed program, would have found nothing at all.
_DOCKER_DESKTOP_REGISTRY_PATHS = (
    r"SOFTWARE\Docker Inc.\Docker",
    r"SOFTWARE\Docker Inc.\Docker\1.0",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Docker Desktop.exe",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop",
)
_REGISTRY_HIVES = ("HKLM", "HKCU")

# All-users and per-user Start menus. Expanded by PowerShell, not by us: this
# process's `%APPDATA%` is the right one, but writing the expansion here would
# hard-code a folder Windows is free to redirect.
_START_MENU_DIRS = (
    r"$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
    r"$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
)


def _docker_desktop_known_paths() -> list[Path]:
    """Every place a known install layout could have put the exe, best first."""
    paths: list[Path] = []
    for var in _DOCKER_DESKTOP_ROOT_VARS:
        root = os.environ.get(var)
        if not root:
            continue
        for subdir in _DOCKER_DESKTOP_SUBDIRS:
            candidate = Path(root, *subdir, DOCKER_DESKTOP_EXE)
            if candidate not in paths:
                paths.append(candidate)
    return paths


def _docker_desktop_probe_command() -> list[str]:
    """PowerShell that prints every path Windows itself associates with Docker Desktop.

    It prints candidates, not an answer: every string value under the registry
    keys above, whatever `Get-Command` resolves, and the target of the Start
    menu shortcut. `find_docker_desktop()` keeps the first line that turns out
    to be a real file, so this never has to be RIGHT about which value means
    what — only complete. That is deliberate: pinning `AppPath` by name is the
    same brittleness as pinning an install path, one level up.

    Read-only by construction (`Get-ItemProperty`, `Get-Command`,
    `Get-ChildItem`, `CreateShortcut`), and `SilentlyContinue` keeps a key that
    does not exist on this machine from taking the rest of the probe with it.
    """
    keys = ", ".join(
        _ps_quote(f"{hive}:\\{path}")
        for hive in _REGISTRY_HIVES
        for path in _DOCKER_DESKTOP_REGISTRY_PATHS
    )
    menus = ", ".join(f'"{folder}"' for folder in _START_MENU_DIRS)
    script = "; ".join(
        (
            "$ErrorActionPreference = 'SilentlyContinue'",
            "foreach ($key in @(" + keys + ")) { $item = Get-ItemProperty -Path $key; "
            "if ($item) { $item.PSObject.Properties | ForEach-Object "
            "{ if ($_.Value -is [string]) { $_.Value } } } }",
            "(Get-Command " + _ps_quote(DOCKER_DESKTOP_EXE) + ").Source",
            "Get-ChildItem -Path "
            + menus
            + " -Filter "
            + _ps_quote(_DOCKER_DESKTOP_SHORTCUT)
            + " -Recurse | ForEach-Object { (New-Object -ComObject WScript.Shell)"
            ".CreateShortcut($_.FullName).TargetPath }",
        )
    )
    return ["powershell.exe", "-NoProfile", "-Command", script]


def _docker_desktop_exe_at(text: str) -> Path | None:
    """One probe line as a real exe — the exe itself or the folder holding it — or None.

    The probe prints whatever the registry holds, which is sometimes the install
    FOLDER (`AppPath`), sometimes the exe (`App Paths`, the shortcut target),
    and often neither (`PSPath`, a version string, an uninstall command line).
    Both shapes are accepted and only a path that is genuinely a file on disk
    survives, so a value name changing between Docker Desktop releases costs
    nothing.
    """
    cleaned = text.strip().strip('"')
    if not cleaned:
        return None
    candidate = Path(cleaned)
    if candidate.name.casefold() == DOCKER_DESKTOP_EXE.casefold():
        return candidate if candidate.is_file() else None
    exe = candidate / DOCKER_DESKTOP_EXE
    return exe if exe.is_file() else None


def find_docker_desktop(run: RunCmd | None = None) -> Path | None:
    r"""Where Docker Desktop actually is on this PC, or None if it is not installed.

    The start step used to be `Start-Process 'Docker Desktop'`. That string is
    neither a path nor a command: ShellExecute resolves a bare name through PATH
    and the App Paths registry, and Docker Desktop's installer registers
    neither — it only adds `...\Docker\resources\bin`, which holds the `docker`
    CLI, not the app. Measured by hand on a clean Windows 11 VM (2026-08-22):
    the step exits 1 with "The system cannot find the file specified" on ANY
    machine, installed or not. So provisioning downloaded Docker Desktop,
    installed it silently, and then never started it — the user watched a
    3-minute poll and was told "the engine has not answered yet" with a
    perfectly good install sitting on disk, and the one thing that would have
    fixed it (open Docker Desktop) was the thing the app claimed to have done.

    Hard-coding `C:\Program Files\Docker\Docker\Docker Desktop.exe` in its place
    fixes one machine. It would not have fixed the machine this was written on:
    Docker Desktop 4.83.0 there is a per-user install under
    `%LOCALAPPDATA%\Programs\DockerDesktop`, with nothing under Program Files,
    no `HKLM:\SOFTWARE\Docker Inc.` key, no `App Paths` entry in either hive and
    nothing named `Docker Desktop.exe` on PATH. The single source that answered
    was the Start menu shortcut (2026-08-23).

    So Windows is asked first and the known layouts are only the fallback —
    the same order, for the same measured reason, as `docker_programs()`: what
    the machine reports is right for a custom `--installation-dir` and right for
    whatever layout the next release ships, while a hardcoded list is a guess
    that was already wrong once here. Measured cost of asking: 0.60 s, once per
    provisioning run, immediately before starting a program that then takes
    tens of seconds to bring its engine up. The list survives underneath, for
    the box whose PowerShell is locked down or missing.

    `winreg` — used a few functions up by `_registry_search_path()` — would read
    the registry in-process and is deliberately not used: the answer that
    actually worked came from a Start menu `.lnk`, which needs a `WScript.Shell`
    COM call, and PATH, which needs `Get-Command`. One PowerShell probe answers
    all three in one spawn and goes through the `run` seam every test here
    already fakes; `winreg` would answer the one source that was empty.
    """
    do: RunCmd = run if run is not None else (lambda argv: runner.run(argv))
    try:
        proc = do(_docker_desktop_probe_command())
    except OSError as exc:
        logger.debug(f"could not ask Windows where Docker Desktop is: {exc}")
    else:
        for line in proc.stdout.splitlines():
            exe = _docker_desktop_exe_at(line)
            if exe is not None:
                logger.info(f"Docker Desktop found by asking Windows: {exe}")
                return exe
    for candidate in _docker_desktop_known_paths():
        if candidate.is_file():
            logger.info(f"Docker Desktop found at a known install location: {candidate}")
            return candidate
    logger.info("Docker Desktop is not installed anywhere this machine knows about")
    return None


def _start_docker_desktop_command(exe: Path) -> list[str]:
    """`Start-Process <exe>`, with the path quoted (Program Files has a space in it)."""
    return ["powershell.exe", "-NoProfile", "-Command", f"Start-Process {_ps_quote(exe)}"]


def ensure_wsl2(*, run: RunCmd | None = None, dry_run: bool = False) -> ProvisionReport:
    """Ensure WSL2 exists on Windows (`wsl --status`; else `wsl --install --no-distribution`).

    Installing WSL needs elevation and a reboot; that is reported as
    `reboot_required`, and `docker_ready` stays False until the next run.
    """
    do: RunCmd = run if run is not None else (lambda argv: runner.run(argv))
    current = detect()
    if current != "windows":
        return ProvisionReport(
            current, done=("WSL2 not needed on this OS",), docker_ready=docker_ready(do)
        )
    try:
        status = do(["wsl.exe", "--status"])
    except OSError:
        status = None
    if status is not None and status.returncode == 0:
        return ProvisionReport("windows", done=("WSL2 present",), docker_ready=docker_ready(do))
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "Start-Process wsl.exe -Verb RunAs -Wait -ArgumentList '--install','--no-distribution'",
    ]
    if dry_run:
        return ProvisionReport(
            "windows", skipped=(f"(dry run) {' '.join(cmd)}",), reboot_required=True
        )
    try:
        proc = do(cmd)
    except OSError as exc:
        return ProvisionReport(
            "windows", skipped=(f"wsl --install: {exc}",), manual_steps=(_MANUAL_WSL,)
        )
    if proc.returncode != 0:
        return ProvisionReport(
            "windows",
            skipped=(f"wsl --install: exit {proc.returncode} {proc.stderr.strip()}",),
            manual_steps=(_MANUAL_WSL,),
        )
    return ProvisionReport(
        "windows",
        done=("wsl --install --no-distribution",),
        manual_steps=("Reboot Windows to finish enabling WSL2, then start Yu'lon again.",),
        reboot_required=True,
    )


def _ensure_docker_windows(
    do: RunCmd,
    which: Callable[[str], str | None] | None,
    download: Downloader,
    dry_run: bool,
    wait_seconds: float,
    cancel: threading.Event | None = None,
) -> ProvisionReport:
    wsl = ensure_wsl2(run=do, dry_run=dry_run)
    if wsl.reboot_required or (wsl.skipped and not dry_run and not wsl.done):
        return wsl
    find = which if which is not None else _which
    done = list(wsl.done)
    skipped = list(wsl.skipped)
    if not find("docker"):
        installer = config_dir() / "downloads" / "Docker Desktop Installer.exe"
        install_cmd = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            # A quote inside a PowerShell '...' literal is escaped by doubling it:
            # an apostrophe in the profile path must not end the string, in a
            # command that runs elevated (review finding, 2026-08-21).
            f"Start-Process {_ps_quote(installer)} -Verb RunAs -Wait -ArgumentList "
            "'install','--quiet','--accept-license','--backend=wsl-2'",
        ]
        if dry_run:
            skipped += [
                f"(dry run) download {DOCKER_DESKTOP_WINDOWS_URL}",
                f"(dry run) {' '.join(install_cmd)}",
            ]
            return ProvisionReport("windows", tuple(done), tuple(skipped))
        try:
            download(DOCKER_DESKTOP_WINDOWS_URL, installer)
            done.append(f"downloaded Docker Desktop installer → {installer}")
        except OSError as exc:
            return ProvisionReport(
                "windows",
                tuple(done),
                (*skipped, f"download Docker Desktop: {exc}"),
                _download_manual_steps(exc),
            )
        d2, s2 = _run_steps(do, [install_cmd], sudo=False, dry_run=False)
        done += d2
        skipped += s2
        if s2:
            return ProvisionReport(
                "windows",
                tuple(done),
                tuple(skipped),
                ("Docker Desktop's installer did not finish; run the downloaded installer.",),
            )
    if dry_run:
        # The probe is read-only, but `dry_run` means "no child processes", and
        # the exe cannot be named here without running it.
        skipped.append(f"(dry run) find {DOCKER_DESKTOP_EXE} and start it")
        return ProvisionReport("windows", tuple(done), tuple(skipped))
    exe = find_docker_desktop(do)
    if exe is None:
        skipped.append(
            f"start Docker Desktop: no {DOCKER_DESKTOP_EXE} in Program Files, the registry, "
            "the Start menu or PATH"
        )
        return ProvisionReport(
            "windows",
            tuple(done),
            tuple(skipped),
            (_MANUAL_START_DOCKER_DESKTOP,),
            False,
            # Nothing was started, so nothing is about to start answering. Ask
            # once (a daemon could have come up while the installer ran) instead
            # of holding the user on a poll that cannot succeed — the old code
            # spent the full 180 s here on every failed start.
            _wait_docker_ready(do, 0.0, _DOCKER_READY_POLL_SECONDS, cancel),
        )
    d3, s3 = _run_steps(do, [_start_docker_desktop_command(exe)], sudo=False, dry_run=False)
    done += d3
    skipped += s3
    ready = _wait_docker_ready(do, wait_seconds, _DOCKER_READY_POLL_SECONDS, cancel)
    manual: tuple[str, ...] = ()
    if not ready:
        manual = (
            f"Docker Desktop is installed ({exe}) but its engine has not answered yet — open "
            "Docker Desktop, wait for 'Engine running', then try again.",
        )
    return ProvisionReport("windows", tuple(done), tuple(skipped), manual, False, ready)


def _ensure_docker_macos(
    do: RunCmd,
    download: Downloader,
    dry_run: bool,
    wait_seconds: float,
    cancel: threading.Event | None = None,
) -> ProvisionReport:
    import platform as _py_platform

    arch = "arm64" if _py_platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    url = DOCKER_DESKTOP_MAC_URLS[arch]
    dmg = config_dir() / "downloads" / "Docker.dmg"
    mount = "/Volumes/YulonDocker"
    commands = [
        ["hdiutil", "attach", str(dmg), "-nobrowse", "-mountpoint", mount],
        ["cp", "-R", f"{mount}/Docker.app", "/Applications/"],
        ["hdiutil", "detach", mount],
        ["open", "-a", "Docker"],
    ]
    done: list[str] = []
    skipped: list[str] = []
    if Path("/Applications/Docker.app").exists():
        done.append("Docker.app already in /Applications")
        commands = commands[-1:]
    elif dry_run:
        skipped.append(f"(dry run) download {url}")
    else:
        try:
            download(url, dmg)
            done.append(f"downloaded Docker Desktop → {dmg}")
        except OSError as exc:
            return ProvisionReport(
                "macos",
                tuple(done),
                (f"download Docker Desktop: {exc}",),
                _download_manual_steps(exc),
            )
    d, s = _run_steps(do, commands, sudo=False, dry_run=dry_run)
    done += d
    skipped += s
    ready = (
        False
        if dry_run
        else _wait_docker_ready(do, wait_seconds, _DOCKER_READY_POLL_SECONDS, cancel)
    )
    manual: tuple[str, ...] = ()
    if not ready and not dry_run:
        manual = (
            "Docker Desktop is installed; open it once, accept its prompts, wait for the whale "
            "icon, then try again.",
        )
    return ProvisionReport("macos", tuple(done), tuple(skipped), manual, False, ready)


# ------------------------------------------------- machine facts (roadmap 6.2)
# What the native install engine's preflight needs to know about THIS machine,
# and nothing about any game (style-guide §3). Every function here answers
# `None` for "could not be established", which `catalog/preflight.py` renders as
# *unchecked* — never as a pass and never as a refusal. A stopped Docker Desktop
# prints zeroes, so a fact that is merely absent must not arrive as a number
# (`rust-prior-art.md` §3).


@dataclass(frozen=True)
class VmResources:
    """What the Linux VM the containers run in actually has.

    On Windows and macOS this is the VM's allowance, NOT the host's hardware —
    a 32 GB Mac whose Docker Desktop is set to 4 GB compiles AzerothCore into
    the OOM killer, and asking the host would have called that fine. On Linux
    the engine is the host, so the two coincide.
    """

    memory_bytes: int
    cpus: int


def vm_resources(run: RunCmd | None = None) -> VmResources | None:
    """Memory and CPU count the container engine reports, or None if it did not answer.

    `docker info` rather than `psutil`/`os.cpu_count()` for the reason above:
    the number that decides whether a build survives is the engine's, and only
    the engine knows it.

    Zeroes are treated as no answer. A stopped Docker Desktop still prints a
    well-formed JSON document with `MemTotal: 0`, and a preflight that believed
    it would refuse every install on the machine with "0 GB of RAM" — the exact
    fabricated refusal the tri-state discipline exists to prevent.
    """
    do = run if run is not None else (lambda argv: runner.run(argv))
    program = docker_program()
    if program is None:
        return None
    try:
        proc = do([program, "info", "--format", "{{json .}}"])
    except OSError as exc:
        logger.debug(f"could not start {program}: {exc}")
        return None
    if proc.returncode != 0:
        logger.info(f"docker info would not answer, so the VM's size is unknown: {proc.stderr}")
        return None
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        logger.info("docker info did not return JSON, so the VM's size is unknown")
        return None
    if not isinstance(parsed, dict):
        return None
    memory = parsed.get("MemTotal")
    cpus = parsed.get("NCPU")
    if not isinstance(memory, int) or not isinstance(cpus, int) or memory <= 0 or cpus <= 0:
        logger.info(f"docker info reported MemTotal={memory!r} NCPU={cpus!r}; treating as unknown")
        return None
    return VmResources(memory, cpus)


_DOCKER_DESKTOP_SETTINGS_KEYS = ("dataFolder", "DataFolder", "diskPath", "DiskPath")
"""Keys Docker Desktop is believed to store its data root under.

Four spellings because the file has been through several: `rust-prior-art.md`
§3 names `DataFolder`/`dataFolder`/`diskPath`, and the casing differs between
Docker Desktop versions. All four are read and the first present one wins;
absent means the platform default.
"""


def docker_desktop_settings_file() -> Path | None:
    """Where Docker Desktop keeps the settings JSON that names its data root.

    Windows: `%APPDATA%\\Docker\\settings-store.json`, with `settings.json` as
    the older name. **Verified on no machine by this project** — it is read
    defensively and a miss falls through to the default.

    macOS: `~/Library/Group Containers/group.com.docker/settings-store.json` is
    what the design believes, and believing is not knowing (phase6-decisions,
    "Baerthe's list" item 1). It is now *the* input to `docker_desktop_data_root()`'s
    macOS branch (read defensively, falling back to the default `Docker.raw`
    when the file or its keys say nothing) — so the "returns None rather than
    guessed" state is gone, replaced by "resolves, but owes the gate a
    measurement of what 'free space' means against a sparse image".

    Linux: None. There is no Docker Desktop settings store on the path this
    project supports there — the engine is the host's own.
    """
    here = detect()
    if here == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        store = base / "Docker" / "settings-store.json"
        return store if store.is_file() else base / "Docker" / "settings.json"
    if here == "macos":
        return (
            Path.home()
            / "Library"
            / "Group Containers"
            / "group.com.docker"
            / "settings-store.json"
        )
    return None


_MACOS_DOCKER_RAW = (
    "Library",
    "Containers",
    "com.docker.docker",
    "Data",
    "vms",
    "0",
    "data",
    "Docker.raw",
)
"""macOS's default Docker Desktop data file: the sparse disk backing the Linux VM.

Named in `rust-prior-art.md` §4 as one of the three macOS facts the earlier
Rust launcher never implemented ("written fresh"). Believed, not measured by
this project: there is no Mac with Docker Desktop on this side. It is read
only as a fallback target for `preflight`'s free-space measurement, and a miss
is reported as *unchecked*, never as a refusal.
"""


def _macos_default_data_root() -> Path:
    """Docker Desktop's default sparse VM disk on macOS, whether or not it exists yet."""
    return Path.home().joinpath(*_MACOS_DOCKER_RAW)


def docker_desktop_data_root() -> Path | None:
    """The path whose free space decides whether the build fits. None = unknown.

    This is NOT the server directory. On Windows and macOS the images and the
    build cache live inside the Linux VM's disk, so measuring the folder the
    user picked answers for the wrong drive entirely (`rust-prior-art.md` §3) —
    what has to be measured is the host file that backs the VM.

    * Linux: `/var/lib/docker`, which really is a host directory.
    * Windows: the `dataFolder`/`diskPath` in Docker Desktop's settings store,
      falling back to `%LOCALAPPDATA%\\Docker\\wsl` — the WSL2 backend's default
      home for `docker_data`. Believed, not measured on a real box.
    * macOS: the settings store's `diskPath`/`DataFolder`, falling back to
      Docker Desktop's default sparse disk (`Docker.raw`). `preflight` measures
      HOST free space on the volume holding that file — the answer to "can the
      sparse image keep growing", which is the failure a long build actually
      hits. The VM's own *allocation* (the virtual-disk cap that can fill with
      host room to spare) is a different number this app cannot read yet, and
      remains the open question behind the `unchecked` marker until the first
      Mac gate measures it. Both the path and its keys are documented rather
      than observed by this project, so they are read defensively and a miss
      falls through to the default instead of refusing a Mac with plenty of
      room.
    """
    here = detect()
    if here == "linux":
        return Path("/var/lib/docker")
    if here == "macos":
        store = docker_desktop_settings_file()
        configured = _settings_data_folder(store) if store is not None else None
        return configured if configured is not None else _macos_default_data_root()
    store = docker_desktop_settings_file()
    configured = _settings_data_folder(store) if store is not None else None
    if configured is not None:
        return configured
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "Docker" / "wsl") if local else None


def _settings_data_folder(store: Path) -> Path | None:
    """The data root Docker Desktop's settings name, if that file can be read at all."""
    try:
        with store.open(encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"could not read {store}: {exc}")
        return None
    if not isinstance(parsed, dict):
        return None
    for key in _DOCKER_DESKTOP_SETTINGS_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value)
    return None


# Folder-name fragments that mean a cloud sync client owns this directory. A
# 2.4 GB checkout of build artefacts inside one is not a slow install, it is a
# sync client rewriting files under a compiler and a user's quota exhausted
# overnight. Matched case-insensitively against the path's PARTS, so a folder
# genuinely called "OneDrive" anywhere above the install is enough.
_SYNCED_DIR_NAMES = ("onedrive", "dropbox", "google drive", "googledrive", "icloud drive")
# The install scripts refuse these outright (e.g. install-wow-wotlk-fedora.sh's
# `case "$SERVER_DIR" in /|"$HOME"|/root|/tmp|...`). Mirrored here so the GUI
# refuses them at the picker instead of after the user has typed a sudo
# password and waited through Docker discovery, which is where the script's
# refusal actually lands (live gate on clean Fedora 44, 2026-08-25).
_RESERVED_SERVER_DIRS = (
    "/home",
    "/media",
    "/mnt",
    "/opt",
    "/root",
    "/srv",
    "/tmp",
    "/var",
    "/etc",
    "/usr",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
)
# Windows has no shell installer to mirror, so this list answers to nothing but
# the same rule: a reinstall removes the folder it was given, and these are
# folders nobody may hand it. Matched case-insensitively because NTFS is.
_RESERVED_WINDOWS_DIRS = (
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "users",
)
_ICLOUD_MARKER = "com~apple~clouddocs"
"""How iCloud Drive spells itself on disk (`~/Library/Mobile Documents/com~apple~CloudDocs`)."""


def _canonical(path: Path) -> Path:
    """`realpath -m --` in Python: resolve symlinks, tolerate a missing tail.

    The install scripts canonicalise with `realpath -m -- "$SERVER_DIR"` BEFORE
    their `case`, so a purely lexical `os.path.normpath` here cannot deliver the
    one guarantee this mirror exists for. On Fedora Atomic `/home` is a symlink
    to `/var/home`: a picker that returns `/home/pk` and a script that sees
    `/var/home/pk` disagree about whether the path is `$HOME`, and the user pays
    for the disagreement with a sudo password and a wait.

    `strict=False` never raises for a path that does not exist; the guard is for
    the ones that do raise - a symlink loop, or a mount present in the tree but
    not answering.
    """
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return Path(os.path.normpath(str(path)))


def _home_readable() -> bool:
    """Whether `Path.home()` answers at all. It raises when HOME is unset."""
    try:
        Path.home()
    except (RuntimeError, OSError):
        return False
    return True


def _reserved_dir_reason(server_dir: Path) -> str | None:
    """Why this is a place no server may be installed, or None.

    Three shapes, all of which the scripts already refuse and the GUI did not:
    a filesystem root, the user's home itself, and the well-known system trees.
    Installing into any of them means a later `rm -rf` of that path during a
    reinstall, which is why the scripts treat it as fatal rather than a warning.

    The home directory is the one that actually bites: the picker opens there,
    and `server_dir_problem()` used to pass it, so a click-through reached the
    script and died with "Cannot use '/home/pk' as the install location" only
    after the sudo password had been entered.
    """
    lexical = Path(os.path.normpath(str(server_dir)))
    resolved = _canonical(server_dir)
    spellings = (lexical, resolved)
    if any(one.parent == one for one in spellings):
        return (
            f"{server_dir} is the root of a filesystem. Pick a dedicated subfolder inside it, "
            "not the drive itself."
        )
    if _home_readable():
        home = Path.home()
        if any(one in (Path(os.path.normpath(str(home))), _canonical(home)) for one in spellings):
            return (
                f"{server_dir} is your home folder itself. A server install owns the folder it "
                "is given - a reinstall removes it - so pick a dedicated subfolder inside your "
                "home folder instead."
            )
    if any(one.as_posix() in _RESERVED_SERVER_DIRS for one in spellings):
        return (
            f"{server_dir} is a system directory. Pick a folder under your home directory "
            "instead."
        )
    if any(
        len(one.parts) == 2 and one.parts[1].lower() in _RESERVED_WINDOWS_DIRS for one in spellings
    ):
        return (
            f"{server_dir} is a Windows system folder. Pick a folder under your user folder "
            "instead."
        )
    return None


def server_dir_problem(server_dir: Path) -> str | None:
    """Why this folder is a bad place for a server install, in the user's words.

    None means "no known problem", which is not the same as "proved good" —
    `docker.bind_mount_ok()` is the check that cannot be wrong, and this one
    exists to explain *why* a mount would fail, or to catch the failures that
    only show up hours later:

    * a cloud-synced folder (OneDrive, Dropbox, Google Drive, iCloud Drive):
      the sync client rewrites files while the build reads them and uploads a
      multi-gigabyte checkout the user never meant to store;
    * a UNC path (`\\\\server\\share`): Docker Desktop cannot bind-mount one,
      and the failure arrives as an empty directory rather than an error;
    * a mapped network drive: the same, wearing a local drive letter.

    All three are refusals in preflight rather than warnings, because each of
    them fails AFTER the two-to-four-hour build rather than before it.
    """
    text = str(server_dir)
    if text.startswith("\\\\") or text.startswith("//"):
        return (
            f"{server_dir} is a network path. Docker Desktop cannot share one with its Linux "
            "VM, so the install would appear to work and the containers would see an empty "
            "folder. Pick a folder on this machine's own disk."
        )
    lowered = [part.lower() for part in Path(text).parts]
    if any(_ICLOUD_MARKER in part for part in lowered):
        return (
            f"{server_dir} is inside iCloud Drive, which syncs and evicts files while the "
            "server is running. Pick a folder outside it."
        )
    for part in lowered:
        for name in _SYNCED_DIR_NAMES:
            if part == name or part.startswith(f"{name} -"):
                return (
                    f"{server_dir} is inside a cloud-synced folder ({part}). The sync client "
                    "would rewrite files under the compiler and upload the whole checkout. "
                    "Pick a folder outside it."
                )
    mapped = _mapped_network_drive(server_dir)
    if mapped is not None:
        return (
            f"{server_dir} is on {mapped}, a mapped network drive. Docker Desktop cannot share "
            "one with its Linux VM. Pick a folder on this machine's own disk."
        )
    # Last, so a path that is BOTH a drive root and a mapped network drive is
    # refused with the network reason. A bare mapped drive used to be answered
    # "pick a subfolder inside it", which walks the user into a second refusal.
    return _reserved_dir_reason(server_dir)


def _mapped_network_drive(path: Path) -> str | None:
    """The drive letter, if `path` sits on a Windows network drive. None otherwise.

    `GetDriveTypeW` is asked rather than a heuristic about drive letters,
    because a mapped drive is indistinguishable from a local one by name. Off
    Windows there is nothing to ask and the answer is None.
    """
    if detect() != "windows":
        return None
    drive = os.path.splitdrive(str(path))[0]
    if not drive:
        return None
    try:
        import ctypes

        # `getattr`, not `ctypes.windll`: the attribute only exists on Windows,
        # and CI type-checks on Linux, where naming it directly is an error —
        # while a `type: ignore` for that is itself an error when the same
        # checker runs on a developer's Windows box. Same reason
        # `_registry_search_path()` imports `winreg` dynamically.
        kernel32 = getattr(ctypes, "windll").kernel32  # noqa: B009 - Windows-only attribute
        drive_type = kernel32.GetDriveTypeW(f"{drive}\\")
    except (AttributeError, OSError) as exc:  # pragma: no cover - non-Windows or no ctypes
        logger.debug(f"could not ask Windows what kind of drive {drive} is: {exc}")
        return None
    return drive if drive_type == _DRIVE_REMOTE else None


_DRIVE_REMOTE = 4
"""`DRIVE_REMOTE` from winbase.h — what `GetDriveTypeW` returns for a network drive."""


class KeepAwake(Protocol):
    """A held assertion that the machine must not doze off. Released on exit."""

    def __enter__(self) -> None: ...

    def __exit__(self, *exc: object) -> None: ...


@contextmanager
def keep_awake(
    *,
    platform_id: Callable[[], PlatformId] = detect,
    spawn: Callable[[list[str]], subprocess.Popen[bytes]] | None = None,
) -> Iterator[None]:
    """Hold the machine awake for the duration of the block. Best effort, and it says so.

    **What this promises, exactly.** An *idle* machine will not go to sleep
    mid-compile. That is the case that actually eats a four-hour build: nobody
    touches the keyboard for three hours because the build is the whole point.

    **What it cannot promise, and the roadmap's wording overpromises here.**
    Closing the laptop lid still suspends the machine, on both platforms.
    `caffeinate` does not override the lid action without an external display
    and power, and `SetThreadExecutionState` does not either — the lid is a
    power *setting*, and rewriting a user's power settings is not something an
    installer may do behind their back. The lid case is UI copy shown before
    the build starts, not an assertion. This docstring is the flag.

    macOS: `caffeinate -dims -w <our pid>` — a child that dies when we do, so
    there is no cleanup path to forget. Unverified: `caffeinate` ships with the
    OS and `-dims` is believed to be the right assertion set for a Docker
    Desktop VM, and neither claim has been executed on a Mac by this project.

    Windows: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`,
    which is a THREAD-scoped assertion — it must be set and cleared on the same
    thread, and it only holds while that thread lives. That makes the worker
    thread running the install the only correct place to call it, so calling it
    from the main (GUI) thread is refused rather than silently doing nothing
    useful the moment the install moves off it.

    Linux: a no-op for Phase 6. The Linux path still runs the bash installer,
    and `systemd-inhibit` waits for the 6.5 Linux gate rather than being
    written blind here.
    """
    here = platform_id()
    if here == "macos":
        argv = ["caffeinate", "-dims", "-w", str(os.getpid())]
        start = spawn if spawn is not None else _spawn_detached
        try:
            child = start(argv)
        except OSError as exc:
            logger.warning(f"could not hold this Mac awake ({exc}); the build may be interrupted")
            yield
            return
        logger.info(f"holding this Mac awake for the build: {' '.join(argv)}")
        try:
            yield
        finally:
            child.terminate()
        return
    if here == "windows":
        if threading.current_thread() is threading.main_thread():
            raise RuntimeError(
                "keep_awake() must run on the worker thread doing the install: Windows scopes "
                "the assertion to the thread that set it, so holding it on the GUI thread would "
                "claim a guarantee the install does not have."
            )
        with _keep_awake_windows():
            yield
        return
    logger.debug(f"keep_awake() is a no-op on {here}")
    yield


def _spawn_detached(argv: list[str]) -> subprocess.Popen[bytes]:
    """Start a helper process we do not read from and will terminate ourselves."""
    return subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # Sanitised like every other spawn: this one starts `systemd-inhibit`
        # or `caffeinate`, and a frozen launcher that hands them its own
        # libraries gets a keep-awake that silently never starts. Missed on the
        # first pass of the library-path fix precisely because it is the one
        # spawn site not in `runner` and not named in `creationflags()`'s list.
        env=runner.child_env(),
        creationflags=runner.creationflags(),
    )


_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
"""`SetThreadExecutionState` flags: keep the assertion until cleared; no sleep.

`ES_DISPLAY_REQUIRED` is deliberately NOT among them. Keeping a laptop's screen
lit for four hours to compile a server is a battery cost with no benefit — the
build does not need the display, only the CPU.
"""


@contextmanager
def _keep_awake_windows() -> Iterator[None]:
    """Assert `ES_SYSTEM_REQUIRED` on THIS thread, and clear it on the way out.

    Unverified on a real Windows box by this project (roadmap 6.3's gate owns
    that). A failure to set it is logged and the block still runs: an install
    that would have completed must not be refused because a power API said no.
    """
    try:
        import ctypes

        # `getattr` for the reason `_mapped_network_drive()` gives.
        set_state = getattr(ctypes, "windll").kernel32.SetThreadExecutionState  # noqa: B009
    except (AttributeError, OSError) as exc:  # pragma: no cover - non-Windows
        logger.warning(f"could not hold this machine awake ({exc}); the build may be interrupted")
        yield
        return
    if not set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED):
        logger.warning("Windows refused the keep-awake assertion; the build may be interrupted")
    try:
        yield
    finally:
        set_state(_ES_CONTINUOUS)
