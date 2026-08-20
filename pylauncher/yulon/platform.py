"""OS detection + per-OS config dir + silent Docker/WSL2 provisioning stubs.

Platform-specific "ensure a Linux container environment exists" logic lives
here while keeping the rest of the app 100% shared. See pyplan/README.md §3
(the kernel constraint) and §11 (config dir locations).
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Literal

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


def ensure_docker() -> None:
    """Install/verify Docker. Not implemented until Phase 5 (README §3b)."""
    logger.debug("ensure_docker() called")
    raise NotImplementedError("Docker provisioning is implemented in Phase 5")


def ensure_wsl2() -> None:
    """Ensure WSL2 + Docker Desktop on Windows. Not implemented until Phase 5."""
    logger.debug("ensure_wsl2() called")
    raise NotImplementedError("WSL2 provisioning is implemented in Phase 5")


# ---------------------------------------------------------------- networking
# README §13 / roadmap 3.4: the per-OS firewall commands, LAN/public IP
# detection and the WSL2 port proxy live HERE, once, as shared behavior; the
# port numbers come from catalog.json (data), never from this module.

FirewallBackend = Literal["ufw", "firewalld", "netsh", "none"]

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
    """Which firewall tool this host uses: netsh (Windows), ufw, firewalld, or none."""
    if detect() == "windows":
        return "netsh"
    find = which if which is not None else _which
    if find("ufw"):
        return "ufw"
    if find("firewall-cmd"):
        return "firewalld"
    return "none"


def _which(name: str) -> str | None:
    return shutil.which(name)


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


def detect_public_ip(
    http_get: Callable[[str], str] | None = None, services: Iterable[str] = _PUBLIC_IP_SERVICES
) -> str | None:
    """The public IPv4 as seen from the internet (icanhazip/ipify), or None if offline."""
    get = http_get if http_get is not None else _http_get_text
    for url in services:
        try:
            text = get(url).strip()
            IPv4Address(text)
            return text
        except (OSError, ValueError) as exc:
            logger.debug(f"detect_public_ip() via {url} failed: {exc}")
    return None


def _http_get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "yulon"})
    with urllib.request.urlopen(request, timeout=5.0) as resp:
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
