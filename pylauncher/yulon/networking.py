"""Networking auto-setup: LAN and internet play, as a plan the app executes (README §13).

Shared orchestration over `platform.py`'s firewall/IP helpers and the
catalog's port table (roadmap 3.4). `plan()` is pure — it computes exactly
what would be done for a mode, including the router steps the app CANNOT do
(DHCP reservation, TCP port forwarding) and the warnings it detected (ports
bound to 127.0.0.1, CGNAT, a dynamic public IP) — and `apply()` executes the
automatable part: firewall rules (via `sudo -n` on Linux, never a hanging
password prompt), the WSL2 portproxy, and the realmlist UPDATE in the auth
DB through the server's DB container. Every step that could not be done is
reported by name, with the exact commands to paste, instead of failing
silently (style-guide §3: this module holds behavior, `catalog.json` holds
the numbers).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import platform, runner
from yulon.apply import SqlRunner
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

Mode = Literal["lan", "internet"]

_DUCKDNS = "https://www.duckdns.org/"


@dataclass(frozen=True)
class NetworkPlan:
    """Everything a networking setup run would do, decided before anything runs."""

    mode: Mode
    game_id: str
    lan_ip: str | None
    public_ip: str | None
    ports: tuple[int, ...]
    firewall: platform.FirewallBackend
    firewall_commands: tuple[tuple[str, ...], ...]
    portproxy_commands: tuple[tuple[str, ...], ...]
    realmlist_sql: str | None
    client_realmlist: str | None
    manual_steps: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """False if something essential (the LAN IP, or the public IP for internet) is missing."""
        if self.lan_ip is None:
            return False
        return self.mode == "lan" or self.public_ip is not None


@dataclass(frozen=True)
class NetworkReport:
    """What `apply()` did, skipped (with the commands to run by hand), and still needs."""

    plan: NetworkPlan
    done: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    restart_required: bool = False

    @property
    def manual_steps(self) -> tuple[str, ...]:
        return self.plan.manual_steps


def realmlist_sql(entry: CatalogEntry, address: str, local_address: str | None) -> str:
    """The UPDATE that advertises `address` (and `local_address`, if the core has that column)."""
    rl = entry.realmlist
    sets = [f"{rl.address_column}='{_sql_literal(address)}'"]
    if rl.local_address_column and local_address:
        sets.append(f"{rl.local_address_column}='{_sql_literal(local_address)}'")
    return (
        f"UPDATE {entry.databases.auth}.{rl.table} SET {', '.join(sets)} "
        f"WHERE id={rl.realm_id};"
    )


def _sql_literal(ip: str) -> str:
    # IPs/hostnames only: refuse anything that is not a plain address token.
    if not all(ch.isalnum() or ch in ".-:" for ch in ip):
        raise ValueError(f"not an address: {ip!r}")
    return ip


def plan(
    entry: CatalogEntry,
    mode: Mode,
    *,
    lan_ip: str | None = None,
    public_ip: str | None = None,
    bindings: Mapping[int, str] | None = None,
    firewall: platform.FirewallBackend | None = None,
    steamos: bool | None = None,
    wsl: bool | None = None,
    rule_prefix: str = "Yulon",
    detect_lan: Callable[[], str | None] = platform.detect_lan_ip,
    detect_public: Callable[[], str | None] = platform.detect_public_ip,
) -> NetworkPlan:
    """Compute the plan for `mode`. Detection seams default to the real platform probes."""
    ports = (entry.ports.auth, entry.ports.world)
    backend = firewall if firewall is not None else platform.detect_firewall()
    on_steamos = steamos if steamos is not None else platform.is_steamos()
    in_wsl = wsl if wsl is not None else platform.in_wsl()
    lan = lan_ip if lan_ip is not None else detect_lan()
    public = public_ip
    if mode == "internet" and public is None:
        public = detect_public()

    warnings: list[str] = []
    manual: list[str] = []

    fw_cmds = platform.firewall_commands(
        backend, ports, rule_prefix=rule_prefix, steamos=on_steamos
    )
    if backend == "none":
        manual.append(
            "No supported firewall tool (ufw/firewalld) was found; if a firewall is active, "
            f"allow inbound TCP {', '.join(map(str, ports))} by hand."
        )

    proxy_cmds: list[list[str]] = []
    if bindings:
        loopback = [p for p in ports if bindings.get(p, "").startswith("127.")]
        if loopback:
            warnings.append(
                f"ports {loopback} are published on 127.0.0.1, not 0.0.0.0 — other machines "
                "cannot reach them. Fix the compose port bindings"
                + (
                    " (a WSL2 portproxy is added as a stopgap)."
                    if in_wsl or backend == "netsh"
                    else "."
                )
            )
            if (in_wsl or backend == "netsh") and lan:
                proxy_cmds = platform.portproxy_commands(lan, loopback)

    sql: str | None = None
    client_realmlist: str | None = None
    if lan is None:
        warnings.append("could not determine this machine's LAN IP — is it on a network?")
    elif mode == "lan":
        sql = realmlist_sql(entry, lan, lan)
        client_realmlist = lan
    elif public is None:
        warnings.append(
            "could not determine the public IP (offline?) — internet play not configured"
        )
    else:
        sql = realmlist_sql(entry, public, lan)
        client_realmlist = public
        if platform.is_cgnat(public):
            warnings.append(
                f"public address {public} is carrier-grade NAT / private: this ISP connection "
                "cannot accept port forwards. Ask the ISP for a public IP or use a VPN/tunnel."
            )
        manual.append(
            f"Router: give this machine ({lan}) a DHCP reservation so its LAN IP stays fixed."
        )
        manual.append(
            "Router: forward TCP (not UDP) ports "
            + " and ".join(f"{p} → {lan}:{p}" for p in ports)
            + ". Look for 'Port Forwarding', 'Virtual Server' or 'NAT'."
        )
        manual.append(
            f"Public IPs change; if friends suddenly cannot connect, re-run this. For a stable "
            f"name use a free dynamic DNS such as {_DUCKDNS}."
        )
        manual.append(
            f"LAN players still use {lan}; only remote players use {public} "
            "(most home routers have no hairpin NAT)."
        )

    if backend == "netsh":
        manual.append(
            "Windows: set the network profile to Private (Settings → Network & Internet)."
        )

    return NetworkPlan(
        mode=mode,
        game_id=entry.id,
        lan_ip=lan,
        public_ip=public,
        ports=ports,
        firewall=backend,
        firewall_commands=tuple(tuple(c) for c in fw_cmds),
        portproxy_commands=tuple(tuple(c) for c in proxy_cmds),
        realmlist_sql=sql,
        client_realmlist=client_realmlist,
        manual_steps=tuple(manual),
        warnings=tuple(warnings),
    )


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def apply(
    network_plan: NetworkPlan,
    *,
    sql: SqlRunner | None,
    run: Runner | None = None,
    elevate: bool = True,
) -> NetworkReport:
    """Execute the automatable part of `network_plan`; report the rest by name.

    Linux firewall commands run under `sudo -n` (non-interactive): if sudo
    needs a password the step is SKIPPED with the exact command to paste, never
    blocked on an invisible prompt. The realmlist UPDATE needs a `SqlRunner`
    for the server's auth DB; without one it is skipped and reported.
    """
    do = run if run is not None else (lambda argv: runner.run(argv))
    done: list[str] = []
    skipped: list[str] = []
    linux = network_plan.firewall in ("ufw", "firewalld")

    for cmd in network_plan.firewall_commands + network_plan.portproxy_commands:
        argv = list(cmd)
        if linux and elevate:
            argv = ["sudo", "-n", *argv]
        try:
            proc = do(argv)
        except OSError as exc:
            skipped.append(f"{' '.join(cmd)}: {exc}")
            continue
        if proc.returncode == 0:
            done.append(" ".join(cmd))
        else:
            skipped.append(
                f"{' '.join(cmd)}: exit {proc.returncode} {proc.stderr.strip()} — run it by hand"
                + (" with sudo" if linux else " in an Administrator PowerShell")
            )

    restart = False
    if network_plan.realmlist_sql is not None:
        if sql is None:
            skipped.append(f"realmlist not updated (no DB access): {network_plan.realmlist_sql}")
        else:
            sql.run_statement("auth", network_plan.realmlist_sql)
            done.append(f"realmlist → {network_plan.client_realmlist}")
            restart = True

    logger.info(
        f"networking {network_plan.mode} for {network_plan.game_id}: "
        f"{len(done)} done, {len(skipped)} skipped, {len(network_plan.manual_steps)} manual"
    )
    return NetworkReport(
        plan=network_plan, done=tuple(done), skipped=tuple(skipped), restart_required=restart
    )


def write_client_realmlist(
    client_dir: Path, address: str, realmlist_file: str = "realmlist.wtf"
) -> Path:
    """Set `set realmlist <address>` in the user's own client (README §13 LAN step 3).

    Finds the file under `Data/<locale>/` (retail layout) or at the top level
    (repack layout); writes the first one found, creating `Data/enUS/` if none.
    """
    candidates = sorted((client_dir / "Data").glob(f"*/{realmlist_file}")) + [
        client_dir / realmlist_file
    ]
    target = next(
        (c for c in candidates if c.is_file()), client_dir / "Data" / "enUS" / realmlist_file
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        target.read_text(encoding="utf-8", errors="replace").splitlines()
        if target.is_file()
        else []
    )
    kept = [ln for ln in lines if not ln.strip().lower().startswith("set realmlist")]
    target.write_text(
        "\n".join([f"set realmlist {address}", *kept]).rstrip("\n") + "\n", encoding="utf-8"
    )
    return target
