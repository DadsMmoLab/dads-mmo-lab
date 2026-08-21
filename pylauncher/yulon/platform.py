"""OS detection + per-OS config dir + silent Docker/WSL2 provisioning stubs.

Platform-specific "ensure a Linux container environment exists" logic lives
here while keeping the rest of the app 100% shared. See pyplan/README.md §3
(the kernel constraint) and §11 (config dir locations).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
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
_MANUAL_WSL = (
    "Open an Administrator PowerShell and run: wsl --install --no-distribution, then reboot."
)


class ProvisionError(RuntimeError):
    """Provisioning hit something it cannot work around (message is user-readable)."""


@dataclass(frozen=True)
class ProvisionReport:
    """What `ensure_docker()` / `ensure_wsl2()` did, and what is still needed."""

    platform: PlatformId
    done: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    manual_steps: tuple[str, ...] = ()
    reboot_required: bool = False
    docker_ready: bool = False

    @property
    def ok(self) -> bool:
        """True when a daemon answers and nothing is left for the user to do first."""
        return self.docker_ready and not self.reboot_required


RunCmd = Callable[[list[str]], subprocess.CompletedProcess[str]]
Downloader = Callable[[str, Path], Path]


def docker_ready(run: RunCmd | None = None) -> bool:
    """True if `docker info` succeeds (daemon reachable); False if binary/daemon is missing."""
    do = run if run is not None else (lambda argv: runner.run(argv))
    try:
        return do(["docker", "info"]).returncode == 0
    except OSError:
        return False


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


def docker_engine_commands(pm: PackageManager, *, steamos: bool, user: str) -> list[list[str]]:
    """The (sudo-less) commands that install + enable Docker Engine via `pm` for `user`."""
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
    return [
        *install,
        ["systemctl", "enable", "--now", "docker"],
        ["usermod", "-aG", "docker", user],
    ]


def _urllib_download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "yulon"})
    with urllib.request.urlopen(request, timeout=60.0) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)
    return dest


def _wait_docker_ready(run: RunCmd, timeout: float, poll: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if docker_ready(run):
            return True
        time.sleep(poll)
    return docker_ready(run)


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
    download: Downloader = _urllib_download,
    dry_run: bool = False,
    user: str | None = None,
    wait_seconds: float = _DOCKER_READY_TIMEOUT_SECONDS,
) -> ProvisionReport:
    """Make sure a Docker daemon is reachable, installing what the OS needs (README §3b).

    Linux: Docker Engine through the distro package manager (under `sudo -n`; a
    password-needing sudo is a reported skip with the commands to paste). The
    docker group change needs a re-login — reported, never hidden. Windows:
    WSL2 (`ensure_wsl2()`) then Docker Desktop (download + silent install,
    elevated). macOS: Docker Desktop (download .dmg, copy Docker.app, open it).
    Returns a `ProvisionReport`; with `dry_run=True` nothing runs and the report
    lists every step as skipped so the UI can show the plan.
    """
    do: RunCmd = run if run is not None else (lambda argv: runner.run(argv))
    current = detect()
    if docker_ready(do):
        logger.info("ensure_docker(): daemon already reachable")
        return ProvisionReport(current, done=("docker already running",), docker_ready=True)
    if current == "linux":
        who = user or os.environ.get("USER") or os.environ.get("USERNAME") or "deck"
        return _ensure_docker_linux(do, which, dry_run, who, wait_seconds)
    if current == "windows":
        return _ensure_docker_windows(do, which, download, dry_run, wait_seconds)
    return _ensure_docker_macos(do, download, dry_run, wait_seconds)


def _ensure_docker_linux(
    do: RunCmd,
    which: Callable[[str], str | None] | None,
    dry_run: bool,
    user: str,
    wait_seconds: float,
) -> ProvisionReport:
    pm = linux_package_manager(which)
    if pm is None:
        return ProvisionReport(
            "linux",
            manual_steps=(
                "No supported package manager (pacman/apt/dnf/zypper) found. Install Docker "
                "Engine by hand: https://docs.docker.com/engine/install/",
            ),
        )
    commands = docker_engine_commands(pm, steamos=is_steamos(), user=user)
    done, skipped = _run_steps(do, commands, sudo=True, dry_run=dry_run)
    ready = False if dry_run else _wait_docker_ready(do, min(wait_seconds, 30.0), 2.0)
    manual = [
        f"Log out and back in (or run `newgrp docker`) so {user} can use Docker without sudo."
    ]
    if skipped and not dry_run:
        failed = "; ".join(s.split(":")[0] for s in skipped)
        manual.insert(
            0, f"Some steps needed a password; run them in a terminal with sudo: {failed}"
        )
    return ProvisionReport("linux", tuple(done), tuple(skipped), tuple(manual), False, ready)


def _ps_quote(value: object) -> str:
    """`value` as a PowerShell single-quoted literal (inner quotes doubled)."""
    return SINGLE_QUOTE + str(value).replace(SINGLE_QUOTE, SINGLE_QUOTE * 2) + SINGLE_QUOTE


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
                (f"download Docker Desktop: {exc}",),
                (_MANUAL_DOCKER_DESKTOP,),
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
    start_cmd = ["powershell.exe", "-NoProfile", "-Command", "Start-Process 'Docker Desktop'"]
    d3, s3 = _run_steps(do, [start_cmd], sudo=False, dry_run=dry_run)
    done += d3
    skipped += s3
    ready = False if dry_run else _wait_docker_ready(do, wait_seconds, _DOCKER_READY_POLL_SECONDS)
    manual: tuple[str, ...] = ()
    if not ready and not dry_run:
        manual = (
            "Docker Desktop was installed but its engine has not answered yet — open Docker "
            "Desktop, wait for 'Engine running', then try again.",
        )
    return ProvisionReport("windows", tuple(done), tuple(skipped), manual, False, ready)


def _ensure_docker_macos(
    do: RunCmd, download: Downloader, dry_run: bool, wait_seconds: float
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
                (_MANUAL_DOCKER_DESKTOP,),
            )
    d, s = _run_steps(do, commands, sudo=False, dry_run=dry_run)
    done += d
    skipped += s
    ready = False if dry_run else _wait_docker_ready(do, wait_seconds, _DOCKER_READY_POLL_SECONDS)
    manual: tuple[str, ...] = ()
    if not ready and not dry_run:
        manual = (
            "Docker Desktop is installed; open it once, accept its prompts, wait for the whale "
            "icon, then try again.",
        )
    return ProvisionReport("macos", tuple(done), tuple(skipped), manual, False, ready)
